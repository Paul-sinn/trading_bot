"""실주문 실행 워커 v1 **scaffold** — 기본 비활성, 실주문 없음.

WOULD_SUBMIT/accepted_dry_run intent를 받아 "실주문이 허용될지"만 판정한다. 현재 단계엔 실 MCP
주문 경로가 결선돼 있지 않다:
- `RealRobinhoodOrderExecutor`는 어떤 경우에도 `RealExecutionDisabled`를 던진다(실 write 미도달).
- `MockOrderExecutor`는 **테스트 전용** — 가짜 broker_order_id만 돌려준다(브로커 미접촉).

CRITICAL 불변식(이 task):
- `real_order_placed=False`, `real_orders_placed=0` 항상. live_auto/실주문 없음.
- Robinhood write/order/cancel/review MCP 도구를 절대 호출하지 않는다(import조차 하지 않음).
- 모든 게이트(enable·arm·fresh snapshot·buying_power·cap·daily·dup·symbol·equity·limit-buy·
  no-options·no-sell·idempotency)를 통과해도 실 제출은 하지 않는다(scaffold → REAL_READY_DRY_RUN).
- 검증·greenlight 전 라이브 금지(헌장 §3/§10).

spec: specs/broker_snapshot_bridge.md
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale, latest_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_arm import RealOrderArm, arm_state, is_armed, read_arm

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
EXECUTION_RECEIPTS_LOG = "real_execution_receipts.jsonl"

ExecutionDecision = Literal["REAL_BLOCKED", "REAL_READY_DRY_RUN", "MOCK_SUBMITTED", "REAL_SUBMITTED"]
RECEIPT_MODE = "real_execution_scaffold"


class RealExecutionDisabled(RuntimeError):
    """실주문 실행 경로가 비활성/미결선임을 알리는 예외(scaffold).

    실 executor의 제출 메서드는 이 예외만 던진다 — 실 MCP write가 도달 불가함을 보장(fail-closed).
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# --- 주문 executor 인터페이스 (실 제출 경로는 미결선) ---
@runtime_checkable
class OrderExecutor(Protocol):
    name: str

    def submit_limit_buy(self, *, symbol: str, quantity: float, limit_price: float) -> dict: ...


class RealRobinhoodOrderExecutor:
    """실 Robinhood executor 골격 — **항상 RealExecutionDisabled**(실 write 미결선).

    실제 MCP 주문 도구(place_equity_order 등)를 import/호출하지 않는다. 실 결선은 검증·greenlight
    이후 ExecutionGate + 수동 arm 뒤의 별도 phase에서만 한다.
    """

    name = "real_robinhood"

    def submit_limit_buy(self, *, symbol: str, quantity: float, limit_price: float) -> dict:
        raise RealExecutionDisabled(
            "real order execution path is not wired (scaffold). No Robinhood write tool is reachable."
        )


class MockOrderExecutor:
    """**테스트 전용** mock executor — 브로커 미접촉, 가짜 broker_order_id만 반환."""

    name = "mock"

    def submit_limit_buy(self, *, symbol: str, quantity: float, limit_price: float) -> dict:
        return {"broker_order_id": f"MOCK-{uuid.uuid4().hex[:12]}", "symbol": symbol}


# --- readiness 판정 ---
class ExecutionReadiness(BaseModel):
    ready: bool
    block_reasons: list[str] = Field(default_factory=list)


def _has_open_buy(snapshot: BrokerSnapshot, symbol: str) -> bool:
    for order in snapshot.open_orders:
        if isinstance(order, dict) and order.get("symbol") == symbol and str(order.get("side", "")).lower() == "buy":
            return True
    return False


def is_market_open(now: datetime | None = None) -> bool:
    """미국 주식 정규장 대략 판정(평일 13:30–20:00 UTC ≈ 9:30–16:00 ET). scaffold용 근사."""
    now = now or _now()
    if now.weekday() >= 5:  # 토/일
        return False
    return time(13, 30) <= now.timetz().replace(tzinfo=None) < time(20, 0)


def evaluate_readiness(
    intent: OrderIntent,
    *,
    settings: Settings,
    arm: RealOrderArm | None,
    snapshot: BrokerSnapshot | None,
    daily_real_count: int,
    executed_keys: set[str],
    now: datetime | None = None,
    market_open: bool | None = None,
) -> ExecutionReadiness:
    """실주문 허용 여부 게이트(실 write 호출 없음 — 로컬 상태만). 모든 위반을 수집한다."""
    now = now or _now()
    reasons: list[str] = []
    notional = intent.planned_notional_usd

    # 마스터 스위치
    if not settings.enable_real_order_execution:
        reasons.append("ENABLE_REAL_ORDER_EXECUTION=false")
    # 수동 arm
    if settings.require_manual_arm and not is_armed(arm, now=now):
        reasons.append(f"manual arm {arm_state(arm, now=now)}")
    if arm is not None and arm.allowed_symbol and arm.allowed_symbol != intent.symbol:
        reasons.append(f"arm allowed_symbol 불일치: {arm.allowed_symbol} != {intent.symbol}")
    # 출처 게이트: 전략/라이브스캔 생성 intent(strategy_id == live_strategy_id)만 실주문 가능.
    # 테스트성 intent는 기본 차단 — 첫 주문 수동 테스트 모드를 명시적으로 켤 때만 예외(arm TTL이 기한 제한).
    if intent.strategy_id != settings.live_strategy_id and not settings.first_order_manual_test_mode:
        reasons.append("test-only intent (전략/라이브스캔 생성 아님) — 실주문 차단")
    # intent 상태/종류
    if intent.execution_gate_status != "accepted_dry_run":
        reasons.append(f"intent not accepted_dry_run: {intent.execution_gate_status}")
    if intent.side != "BUY":
        reasons.append("sell 자동화 미허용 (limit buy only)")
    if not settings.allow_real_sell_orders and intent.side == "SELL":
        reasons.append("ALLOW_REAL_SELL_ORDERS=false")
    if settings.allow_options_trading is False and getattr(intent, "asset_type", "equity") != "equity":
        reasons.append("옵션 미허용 (equity only)")
    if intent.planned_order_type != "limit":
        reasons.append(f"limit buy only: {intent.planned_order_type}")
    # 스냅샷/잔고
    if snapshot is None:
        reasons.append("broker snapshot 없음")
    else:
        # agentic 계정 전용: 워커는 agentic_allowed 계정만 스냅샷한다. 계정 미상(기본 마스크)이면 차단.
        if settings.agentic_account_only and (not snapshot.account_last4 or snapshot.account_last4 == "••••"):
            reasons.append("AGENTIC_ACCOUNT_ONLY: 스냅샷 계정 미상")
        if settings.require_fresh_broker_snapshot_for_real_order and is_stale(
            snapshot, max_age_seconds=settings.broker_snapshot_max_age_seconds, now=now
        ):
            reasons.append("broker snapshot stale")
        bp = snapshot.buying_power
        if notional is not None and bp is not None and notional > bp:
            reasons.append(f"buying_power 부족: {notional} > {bp}")
        if _has_open_buy(snapshot, intent.symbol):
            reasons.append(f"중복 미체결 매수 주문 존재: {intent.symbol}")
    # 한도
    if notional is None:
        reasons.append("notional 없음")
    elif notional > settings.max_notional_per_real_order_usd:
        reasons.append(f"notional > MAX_NOTIONAL_PER_REAL_ORDER: {notional} > {settings.max_notional_per_real_order_usd}")
    if daily_real_count >= settings.max_real_orders_per_day:
        reasons.append("MAX_REAL_ORDERS_PER_DAY 초과")
    # 장시간
    mo = is_market_open(now) if market_open is None else market_open
    if settings.require_market_hours_for_real_order and not mo:
        reasons.append("장시간 아님")
    # 멱등
    if intent.scan_event_key in executed_keys:
        reasons.append("이미 실행됨 (idempotency)")

    return ExecutionReadiness(ready=not reasons, block_reasons=reasons)


# --- 실행 영수증 ---
class RealExecutionReceipt(BaseModel):
    """실행 readiness 영수증. 이 task에선 real_order_placed=False·real_orders_placed=0 항상."""

    receipt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = Field(default_factory=_now_iso)
    source: str = "claude_code_worker"
    mode: str = RECEIPT_MODE
    intent_id: str
    idempotency_key: str
    symbol: str
    side: str = "BUY"
    quantity: float | None = None
    limit_price: float | None = None
    notional: float | None = None
    decision: ExecutionDecision
    reason: str = ""
    block_reasons: list[str] = Field(default_factory=list)
    executor: str = "real_robinhood"
    # 프로덕션 준비도와 테스트/증명 실행을 절대 혼동하지 않기 위한 출처 표식.
    environment: Literal["production", "test"] = "production"
    market_hours_source: Literal["real", "mocked"] = "real"
    is_proof_run: bool = False
    broker_order_id: str | None = None
    real_order_placed: bool = False
    real_orders_placed: int = 0

    def model_post_init(self, _context) -> None:
        # mode는 항상 고정. 실주문 흔적(real_order_placed/real_orders_placed)은 REAL_SUBMITTED(실제
        # 제출된 경우)에만 제공값을 보존하고, 그 외 모든 경로(BLOCKED/READY/MOCK)는 강제로 0으로 박는다.
        object.__setattr__(self, "mode", RECEIPT_MODE)
        if self.decision != "REAL_SUBMITTED":
            object.__setattr__(self, "real_order_placed", False)
            object.__setattr__(self, "real_orders_placed", 0)


def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / EXECUTION_RECEIPTS_LOG


def append_execution_receipt(receipt: RealExecutionReceipt, *, reports_dir: Path | None = None) -> RealExecutionReceipt:
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt.model_dump(), ensure_ascii=False) + "\n")
    try:  # 알림 실패가 기록/실행을 죽이지 않게 흡수. URL 없으면 no-op.
        from backend.app.services.discord_notifier import notify_real_execution

        notify_real_execution(receipt, reports_dir=reports_dir)
    except Exception:  # noqa: BLE001
        pass
    return receipt


def load_execution_receipts(*, limit: int = 50, reports_dir: Path | None = None) -> list[RealExecutionReceipt]:
    limit = max(1, min(int(limit), 500))
    path = _path(reports_dir)
    if not path.exists():
        return []
    out: list[RealExecutionReceipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(RealExecutionReceipt.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_execution_receipt(*, reports_dir: Path | None = None) -> RealExecutionReceipt | None:
    rs = load_execution_receipts(limit=1, reports_dir=reports_dir)
    return rs[-1] if rs else None


def latest_production_receipt(*, reports_dir: Path | None = None) -> RealExecutionReceipt | None:
    """프로덕션(environment=production, 실 시장시간) 영수증 중 가장 최근 1건.

    프로덕션 준비도 판단은 **오직 이 함수**만 쓴다 — mocked 시장시간 proof(test) 영수증은 절대
    프로덕션 latest로 노출되지 않는다.
    """
    prod = [
        r
        for r in load_execution_receipts(limit=500, reports_dir=reports_dir)
        if r.environment == "production" and not r.is_proof_run
    ]
    return prod[-1] if prod else None


def test_proof_count(*, reports_dir: Path | None = None) -> int:
    """test/proof(environment=test) 영수증 수(별도 '테스트/증명 이력' 표시용)."""
    return sum(
        1
        for r in load_execution_receipts(limit=500, reports_dir=reports_dir)
        if r.environment == "test"
    )


def executed_keys(*, reports_dir: Path | None = None) -> set[str]:
    """이미 '제출된'(MOCK_SUBMITTED) idempotency_key 집합. REAL_BLOCKED/READY는 제외(미제출)."""
    return {
        r.idempotency_key
        for r in load_execution_receipts(limit=500, reports_dir=reports_dir)
        if r.decision == "MOCK_SUBMITTED"
    }


def daily_real_order_count(*, reports_dir: Path | None = None, now: datetime | None = None) -> int:
    """오늘(UTC) 실제 제출된(REAL_SUBMITTED) 실주문 수. MAX_REAL_ORDERS_PER_DAY 게이트의 입력."""
    today = (now or _now()).date().isoformat()
    count = 0
    for r in load_execution_receipts(limit=500, reports_dir=reports_dir):
        if r.decision != "REAL_SUBMITTED":
            continue
        if (r.timestamp or "")[:10] == today:
            count += 1
    return count


def build_receipt(
    intent: OrderIntent,
    readiness: ExecutionReadiness,
    *,
    executor: OrderExecutor | None,
    source: str = "claude_code_worker",
    market_hours_source: Literal["real", "mocked"] = "real",
    is_proof_run: bool = False,
) -> RealExecutionReceipt:
    """readiness + executor로 영수증을 만든다. 실 제출 없음(MOCK만 가짜 id).

    출처 표식: mocked 시장시간 또는 mock executor면 test/proof로 기록한다(프로덕션 준비도와 분리).
    """
    proof = is_proof_run or market_hours_source == "mocked" or isinstance(executor, MockOrderExecutor)
    environment: Literal["production", "test"] = "test" if proof else "production"

    def _receipt(
        decision: ExecutionDecision,
        reason: str,
        *,
        block_reasons: list[str] | None = None,
        broker_order_id: str | None = None,
    ) -> RealExecutionReceipt:
        return RealExecutionReceipt(
            source=source,
            intent_id=intent.scan_event_key,
            idempotency_key=intent.scan_event_key,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.planned_quantity,
            limit_price=intent.planned_limit_price,
            notional=intent.planned_notional_usd,
            executor=executor.name if executor else "real_robinhood",
            environment=environment,
            market_hours_source=market_hours_source,
            is_proof_run=proof,
            decision=decision,
            reason=reason,
            block_reasons=block_reasons or [],
            broker_order_id=broker_order_id,
        )

    if not readiness.ready:
        return _receipt(
            "REAL_BLOCKED",
            readiness.block_reasons[0] if readiness.block_reasons else "blocked",
            block_reasons=readiness.block_reasons,
        )
    # 모든 게이트 통과. mock executor면(테스트) 가짜 제출, 아니면 scaffold dry-run(실 제출 없음).
    if isinstance(executor, MockOrderExecutor):
        result = executor.submit_limit_buy(
            symbol=intent.symbol,
            quantity=intent.planned_quantity or 0.0,
            limit_price=intent.planned_limit_price or 0.0,
        )
        return _receipt(
            "MOCK_SUBMITTED",
            "Mock executor (test only) — no real order submitted",
            broker_order_id=result.get("broker_order_id"),
        )
    return _receipt(
        "REAL_READY_DRY_RUN",
        "All checks pass; real execution path not wired (scaffold) — no order submitted",
    )


def process_execution(
    intent: OrderIntent,
    *,
    settings: Settings | None = None,
    reports_dir: Path | None = None,
    executor: OrderExecutor | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
) -> RealExecutionReceipt:
    """단일 intent를 평가해 실행 영수증을 append한다(실주문 없음). 반환=기록된 영수증."""
    settings = settings or Settings()
    arm = read_arm(reports_dir=reports_dir)
    snapshot = latest_snapshot(reports_dir=reports_dir)
    readiness = evaluate_readiness(
        intent,
        settings=settings,
        arm=arm,
        snapshot=snapshot,
        daily_real_count=daily_real_order_count(reports_dir=reports_dir, now=now),
        executed_keys=executed_keys(reports_dir=reports_dir),
        now=now,
        market_open=market_open,
    )
    # market_open이 명시 주입되면 mocked(=test/proof), None이면 production heuristic(=real).
    market_hours_source: Literal["real", "mocked"] = "mocked" if market_open is not None else "real"
    receipt = build_receipt(
        intent, readiness, executor=executor, market_hours_source=market_hours_source
    )
    return append_execution_receipt(receipt, reports_dir=reports_dir)


# --- 읽기 전용 상태 요약(API/UI용) ---
class ExecutionStatus(BaseModel):
    real_execution_enabled: bool
    require_manual_arm: bool
    agentic_account_only: bool = True
    arm_status: str
    arm_expires_at: str | None = None
    max_notional_per_real_order_usd: float
    max_real_orders_per_day: int
    real_orders_today: int = 0
    # 프로덕션 준비도: 오직 environment=production·실 시장시간 영수증만 반영한다.
    latest_decision: str | None = None  # 프로덕션 최신 결정(없으면 null)
    latest_block_reason: str | None = None
    latest_environment: str | None = None  # 항상 production(또는 None)
    # test/proof(mocked 시장시간) 이력은 별도 카운트로만 노출 — 프로덕션 latest로 섞이지 않는다.
    test_proof_count: int = 0
    real_orders_placed: int = 0


def execution_status(*, settings: Settings | None = None, reports_dir: Path | None = None) -> ExecutionStatus:
    """실행 준비 상태 요약(읽기 전용 — MCP/주문 없음).

    `latest_decision`/`latest_block_reason`은 **프로덕션 영수증만** 반영한다. mocked 시장시간 proof
    (environment=test)는 프로덕션 latest로 절대 노출되지 않고 `test_proof_count`로만 집계된다.
    """
    settings = settings or Settings()
    arm = read_arm(reports_dir=reports_dir)
    prod = latest_production_receipt(reports_dir=reports_dir)
    return ExecutionStatus(
        real_execution_enabled=settings.enable_real_order_execution,
        require_manual_arm=settings.require_manual_arm,
        agentic_account_only=settings.agentic_account_only,
        arm_status=arm_state(arm),
        arm_expires_at=arm.expires_at if arm else None,
        max_notional_per_real_order_usd=settings.max_notional_per_real_order_usd,
        max_real_orders_per_day=settings.max_real_orders_per_day,
        real_orders_today=daily_real_order_count(reports_dir=reports_dir),
        latest_decision=prod.decision if prod else None,
        latest_block_reason=(prod.reason if prod and prod.decision == "REAL_BLOCKED" else None),
        latest_environment=prod.environment if prod else None,
        test_proof_count=test_proof_count(reports_dir=reports_dir),
        real_orders_placed=0,
    )
