"""수동 확인 매도 워커 v1 — 확인 문구 게이트 결선, 무장·확인 없으면 비활성.

기존 Agentic 포지션에 대한 매도 readiness를 판정하고, 미래 실 매도 경로를 **결선**한다. 단,
모든 게이트 통과 + 정확한 확인 문구(`CONFIRM_REAL_SELL_1`)가 있어야만 executor 제출을 시도한다.
이 task에선 실 제출이 여전히 불가능하다:
- `RealRobinhoodSellExecutor`는 어떤 경우에도 `RealSellExecutionDisabled`를 던진다(실 write 미도달).
- `MockSellExecutor`는 **테스트 전용** — 가짜 broker_order_id만 돌려준다(브로커 미접촉).
- Robinhood write/order MCP 도구를 import/호출하지 않는다.

CRITICAL 불변식(이 task):
- 실 매도 주문 없음. 프로덕션에선 실 executor가 항상 disabled → 실 제출 경로는 SELL_BLOCKED로 fail-closed.
- 매수 카운터 불변: 모든 receipt `real_order_placed=False`, `real_orders_placed=0`.
- 실 매도 흔적(`real_sell_order_placed=True`, `real_sell_orders_placed=1`)은
  **environment=production·실 시장시간·non-proof SELL_SUBMITTED**에만 허용 — 그 외(mock/test/proof) 강제 0/false.
- 확인 문구 누락/불일치 → SELL_BLOCKED (게이트 통과해도 제출 안 함).
- 공매도 없음 · 옵션 없음 · 지정가 매도만 · Agentic 계좌만 · 정규장만 · 수동 arm 필수.
- 프로덕션 준비도(latest_decision)는 environment=production·실 시장시간 receipt만 반영. mocked proof는 test로 분리.
- 검증·greenlight 전 라이브 금지(헌장 §3/§10).

spec: specs/real_order_v1_checklist.md
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale, latest_snapshot
from backend.app.services.control_flags import ControlFlags, read_control_flags
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_executor import is_market_open
from backend.app.services.real_sell_arm import RealSellArm, is_sell_armed, read_sell_arm, sell_arm_state

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
SELL_RECEIPTS_LOG = "real_sell_receipts.jsonl"

SellDecision = Literal["SELL_BLOCKED", "SELL_READY_DRY_RUN", "SELL_SUBMITTED"]
RECEIPT_MODE = "real_sell_scaffold"

# 실 매도 제출 전 사람이 정확히 입력해야 하는 확인 문구(대소문자·공백 정확히 일치). 이것이 없으면
# 모든 게이트를 통과해도 SELL_BLOCKED. 자동화/봇이 임의로 채울 수 없는 휴먼 게이트.
CONFIRM_REAL_SELL_PHRASE = "CONFIRM_REAL_SELL_1"


class RealSellExecutionDisabled(RuntimeError):
    """실 매도 경로가 비활성/미결선임을 알리는 예외. 실 MCP write 미도달 보장(fail-closed)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# --- 매도 executor 인터페이스 (실 제출 경로는 미결선) ---
@runtime_checkable
class SellExecutor(Protocol):
    name: str

    def submit_limit_sell(
        self, *, symbol: str, quantity: float, limit_price: float, account_id: str | None = ...
    ) -> dict: ...


class RealRobinhoodSellExecutor:
    """실 Robinhood 매도 executor 골격 — **항상 RealSellExecutionDisabled**(실 write 미결선).

    실제 MCP 주문 도구(place_equity_order sell 등)를 import/호출하지 않는다. 확인 문구·게이트가 모두
    통과해 제출이 호출돼도 이 예외로 fail-closed 된다. 실 결선은 검증·greenlight 이후 별도 phase 전용.
    """

    name = "real_robinhood_sell"

    def submit_limit_sell(
        self, *, symbol: str, quantity: float, limit_price: float, account_id: str | None = None
    ) -> dict:
        raise RealSellExecutionDisabled(
            "real sell execution path is not wired. No Robinhood write tool is reachable."
        )


class MockSellExecutor:
    """**테스트 전용** mock 매도 executor — 브로커 미접촉, 가짜 broker_order_id만 반환."""

    name = "mock_sell"

    def submit_limit_sell(
        self, *, symbol: str, quantity: float, limit_price: float, account_id: str | None = None
    ) -> dict:
        return {"broker_order_id": f"MOCK-SELL-{uuid.uuid4().hex[:12]}", "symbol": symbol}


class SellReadiness(BaseModel):
    ready: bool
    block_reasons: list[str] = Field(default_factory=list)


def _position(snapshot: BrokerSnapshot, symbol: str) -> dict | None:
    for p in snapshot.positions:
        if isinstance(p, dict) and p.get("symbol") == symbol:
            return p
    return None


def _has_open_sell(snapshot: BrokerSnapshot, symbol: str) -> bool:
    for o in snapshot.open_orders:
        if isinstance(o, dict) and o.get("symbol") == symbol and str(o.get("side", "")).lower() == "sell":
            return True
    return False


def evaluate_sell_readiness(
    intent: OrderIntent,
    *,
    settings: Settings,
    arm: RealSellArm | None,
    snapshot: BrokerSnapshot | None,
    sold_keys: set[str],
    control_flags: ControlFlags | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
) -> SellReadiness:
    """실 매도 허용 여부 게이트(실 write 호출 없음 — 로컬 상태만). 모든 위반을 수집한다."""
    now = now or _now()
    reasons: list[str] = []
    qty = intent.planned_quantity

    # 마스터 스위치(매도 자동화 기본 off)
    if not settings.allow_real_sell_orders:
        reasons.append("ALLOW_REAL_SELL_ORDERS=false")
    # 수동 매도 arm
    if not is_sell_armed(arm, now=now):
        reasons.append(f"sell arm {sell_arm_state(arm, now=now)}")
    if arm is not None and arm.allowed_symbol and arm.allowed_symbol != intent.symbol:
        reasons.append(f"arm allowed_symbol 불일치: {arm.allowed_symbol} != {intent.symbol}")
    if arm is not None and arm.max_quantity is not None and qty is not None and qty > arm.max_quantity:
        reasons.append(f"arm max_quantity 초과: {qty} > {arm.max_quantity}")
    if arm is not None and arm.min_limit_price is not None and intent.planned_limit_price is not None \
            and intent.planned_limit_price < arm.min_limit_price:
        reasons.append(f"arm min_limit_price 미만: {intent.planned_limit_price} < {arm.min_limit_price}")
    # intent 종류
    if intent.side != "SELL":
        reasons.append(f"side != SELL: {intent.side}")
    if intent.execution_gate_status != "accepted_dry_run":
        reasons.append(f"intent not accepted_dry_run: {intent.execution_gate_status}")
    if intent.planned_order_type != "limit":
        reasons.append(f"limit sell only: {intent.planned_order_type}")
    if settings.allow_options_trading is False and getattr(intent, "asset_type", "equity") != "equity":
        reasons.append("옵션 미허용 (equity only)")
    # snapshot/포지션/수량
    if snapshot is None:
        reasons.append("broker snapshot 없음")
    else:
        if settings.agentic_account_only and (not snapshot.account_last4 or snapshot.account_last4 == "••••"):
            reasons.append("AGENTIC_ACCOUNT_ONLY: 스냅샷 계정 미상")
        if settings.require_fresh_broker_snapshot_for_real_order and is_stale(
            snapshot, max_age_seconds=settings.broker_snapshot_max_age_seconds, now=now
        ):
            reasons.append("broker snapshot stale")
        pos = _position(snapshot, intent.symbol)
        if pos is None:
            reasons.append(f"매도할 포지션 없음: {intent.symbol}")
        else:
            sellable = pos.get("shares_available_for_sells")
            if sellable is None:
                sellable = pos.get("quantity")  # 스냅샷에 held-for-sells 없으면 보유수량으로 폴백
            if qty is None or qty <= 0:
                reasons.append("매도 수량 유한/양수 아님")
            elif sellable is not None and qty > sellable:
                reasons.append(f"매도 수량 > 매도가능수량: {qty} > {sellable}")
        if _has_open_sell(snapshot, intent.symbol):
            reasons.append(f"중복 미체결 매도 주문 존재: {intent.symbol}")
    # 장시간(실/모의)
    mo = is_market_open(now) if market_open is None else market_open
    if settings.require_market_hours_for_real_order and not mo:
        reasons.append("장시간 아님")
    # 멱등
    if intent.scan_event_key in sold_keys:
        reasons.append("이미 매도 실행됨 (idempotency)")
    # control flags(정지/비상정지면 차단). 부재 → fail-closed.
    if control_flags is None:
        reasons.append("control_flags 없음 (fail-closed)")
    else:
        if control_flags.emergency_halt:
            reasons.append("emergency_halt=true")
        if control_flags.block_new_orders:
            reasons.append("block_new_orders=true")

    return SellReadiness(ready=not reasons, block_reasons=reasons)


class RealSellExecutionReceipt(BaseModel):
    """매도 readiness 영수증. 이 task에선 실 매도 흔적 0(broker_order_id None, real_sell_order_placed False)."""

    receipt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = Field(default_factory=_now_iso)
    source: str = "claude_code_worker"
    mode: str = RECEIPT_MODE
    intent_id: str
    idempotency_key: str
    symbol: str
    side: str = "SELL"
    quantity: float | None = None
    limit_price: float | None = None
    notional: float | None = None
    decision: SellDecision
    reason: str = ""
    block_reasons: list[str] = Field(default_factory=list)
    executor: str = "real_robinhood_sell"
    environment: Literal["production", "test"] = "production"
    market_hours_source: Literal["real", "mocked"] = "real"
    is_proof_run: bool = False
    broker_order_id: str | None = None
    real_order_placed: bool = False
    real_orders_placed: int = 0  # 매수 카운터 — 매도로 변하지 않음
    real_sell_order_placed: bool = False
    real_sell_orders_placed: int = 0

    def model_post_init(self, _context) -> None:
        object.__setattr__(self, "mode", RECEIPT_MODE)
        object.__setattr__(self, "real_order_placed", False)  # 매도 receipt은 매수 플래그를 안 건드림
        object.__setattr__(self, "real_orders_placed", 0)
        # 실 매도 흔적(placed=True/1)은 **진짜 실 제출**에만 보존한다:
        # environment=production · 실 시장시간(real) · proof 아님 · decision=SELL_SUBMITTED.
        # mock/test/proof 제출은 SELL_SUBMITTED라도 카운터를 0/false로 강제(프로덕션 집계와 분리).
        is_real_submit = (
            self.decision == "SELL_SUBMITTED"
            and self.environment == "production"
            and self.market_hours_source == "real"
            and not self.is_proof_run
        )
        if not is_real_submit:
            object.__setattr__(self, "real_sell_order_placed", False)
            object.__setattr__(self, "real_sell_orders_placed", 0)
        # broker_order_id는 제출된 receipt(mock 포함)에만 남기고, 그 외(BLOCKED/READY) None 강제.
        if self.decision != "SELL_SUBMITTED":
            object.__setattr__(self, "broker_order_id", None)


def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / SELL_RECEIPTS_LOG


def append_sell_receipt(receipt: RealSellExecutionReceipt, *, reports_dir: Path | None = None) -> RealSellExecutionReceipt:
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt.model_dump(), ensure_ascii=False) + "\n")
    return receipt


def load_sell_receipts(*, limit: int = 50, reports_dir: Path | None = None) -> list[RealSellExecutionReceipt]:
    limit = max(1, min(int(limit), 500))
    path = _path(reports_dir)
    if not path.exists():
        return []
    out: list[RealSellExecutionReceipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(RealSellExecutionReceipt.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_production_sell_receipt(*, reports_dir: Path | None = None) -> RealSellExecutionReceipt | None:
    """프로덕션(environment=production, 실 시장시간) 매도 receipt 중 최신. mocked proof는 제외."""
    prod = [
        r for r in load_sell_receipts(limit=500, reports_dir=reports_dir)
        if r.environment == "production" and not r.is_proof_run
    ]
    return prod[-1] if prod else None


def sold_keys(*, reports_dir: Path | None = None) -> set[str]:
    """이미 매도 제출(SELL_SUBMITTED)된 idempotency_key 집합."""
    return {
        r.idempotency_key for r in load_sell_receipts(limit=500, reports_dir=reports_dir)
        if r.decision == "SELL_SUBMITTED"
    }


def build_sell_receipt(
    intent: OrderIntent,
    readiness: SellReadiness,
    *,
    source: str = "claude_code_worker",
    market_hours_source: Literal["real", "mocked"] = "real",
) -> RealSellExecutionReceipt:
    """readiness로 매도 영수증을 만든다. 실 제출 없음(scaffold → SELL_READY_DRY_RUN/SELL_BLOCKED)."""
    proof = market_hours_source == "mocked"
    environment: Literal["production", "test"] = "test" if proof else "production"

    def _receipt(decision: SellDecision, reason: str, *, block_reasons: list[str] | None = None) -> RealSellExecutionReceipt:
        return RealSellExecutionReceipt(
            source=source, intent_id=intent.scan_event_key, idempotency_key=intent.scan_event_key,
            symbol=intent.symbol, side="SELL", quantity=intent.planned_quantity,
            limit_price=intent.planned_limit_price, notional=intent.planned_notional_usd,
            environment=environment, market_hours_source=market_hours_source, is_proof_run=proof,
            decision=decision, reason=reason, block_reasons=block_reasons or [],
        )

    if not readiness.ready:
        return _receipt(
            "SELL_BLOCKED",
            readiness.block_reasons[0] if readiness.block_reasons else "blocked",
            block_reasons=readiness.block_reasons,
        )
    return _receipt(
        "SELL_READY_DRY_RUN",
        "All sell checks pass; real sell path not wired (scaffold) — no sell order submitted",
    )


def process_sell_execution(
    intent: OrderIntent,
    *,
    settings: Settings | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
) -> RealSellExecutionReceipt:
    """단일 매도 intent를 평가해 dry-run 영수증을 append한다(확인 없음 → 절대 제출 안 함)."""
    settings = settings or Settings()
    arm = read_sell_arm(reports_dir=reports_dir)
    snapshot = latest_snapshot(reports_dir=reports_dir)
    flags = read_control_flags(reports_dir=reports_dir)
    readiness = evaluate_sell_readiness(
        intent, settings=settings, arm=arm, snapshot=snapshot,
        sold_keys=sold_keys(reports_dir=reports_dir), control_flags=flags, now=now, market_open=market_open,
    )
    mhs: Literal["real", "mocked"] = "mocked" if market_open is not None else "real"
    receipt = build_sell_receipt(intent, readiness, market_hours_source=mhs)
    return append_sell_receipt(receipt, reports_dir=reports_dir)


# --- 확인 게이트 결선(실 매도 제출 경로) ---
class SellPreview(BaseModel):
    """실 매도 직전 사람이 검토할 최종 프리뷰. 출력만 — 어떤 주문도 내지 않는다."""

    symbol: str
    side: str = "SELL"
    order_type: str = "LIMIT"
    quantity: float | None = None
    limit_price: float | None = None
    estimated_notional: float | None = None
    account_last4: str | None = None  # Agentic 계좌(마스킹된 last4)만
    current_position_qty: float | None = None
    shares_available_for_sells: float | None = None
    market_hours: str = "regular only"
    confirmation_phrase: str = CONFIRM_REAL_SELL_PHRASE


def build_sell_preview(
    intent: OrderIntent, *, snapshot: BrokerSnapshot | None, settings: Settings | None = None
) -> SellPreview:
    """실 매도 확인 프리뷰를 만든다(읽기 전용 — 주문 없음, 계좌번호는 last4만)."""
    settings = settings or Settings()
    pos = _position(snapshot, intent.symbol) if snapshot is not None else None
    cur_qty = pos.get("quantity") if pos else None
    avail = None
    if pos:
        avail = pos.get("shares_available_for_sells")
        if avail is None:
            avail = pos.get("quantity")
    notional = intent.planned_notional_usd
    if notional is None and intent.planned_quantity is not None and intent.planned_limit_price is not None:
        notional = intent.planned_quantity * intent.planned_limit_price
    return SellPreview(
        symbol=intent.symbol,
        quantity=intent.planned_quantity,
        limit_price=intent.planned_limit_price,
        estimated_notional=notional,
        account_last4=snapshot.account_last4 if snapshot is not None else None,
        current_position_qty=cur_qty,
        shares_available_for_sells=avail,
    )


def build_sell_submit_receipt(
    intent: OrderIntent,
    readiness: SellReadiness,
    *,
    confirmation: str | None,
    executor: SellExecutor | None = None,
    source: str = "claude_code_worker",
    market_hours_source: Literal["real", "mocked"] = "real",
) -> RealSellExecutionReceipt:
    """게이트 + 확인 문구 통과 시에만 executor 제출을 시도한다.

    - 게이트 미통과 → SELL_BLOCKED.
    - 게이트 통과 + 확인 문구 누락/불일치 → SELL_BLOCKED(제출 안 함).
    - 게이트 통과 + 정확한 확인 문구:
        - MockSellExecutor(테스트) → SELL_SUBMITTED(가짜 id, environment=test → 실 카운터 0).
        - 실 executor(기본) → submit_limit_sell이 RealSellExecutionDisabled → SELL_BLOCKED(fail-closed).
    """
    ex = executor or RealRobinhoodSellExecutor()
    proof = market_hours_source == "mocked" or isinstance(ex, MockSellExecutor)
    environment: Literal["production", "test"] = "test" if proof else "production"

    def _receipt(
        decision: SellDecision,
        reason: str,
        *,
        block_reasons: list[str] | None = None,
        broker_order_id: str | None = None,
        real_sell_order_placed: bool = False,
        real_sell_orders_placed: int = 0,
    ) -> RealSellExecutionReceipt:
        return RealSellExecutionReceipt(
            source=source, intent_id=intent.scan_event_key, idempotency_key=intent.scan_event_key,
            symbol=intent.symbol, side="SELL", quantity=intent.planned_quantity,
            limit_price=intent.planned_limit_price, notional=intent.planned_notional_usd,
            environment=environment, market_hours_source=market_hours_source, is_proof_run=proof,
            decision=decision, reason=reason, block_reasons=block_reasons or [],
            broker_order_id=broker_order_id,
            real_sell_order_placed=real_sell_order_placed, real_sell_orders_placed=real_sell_orders_placed,
        )

    if not readiness.ready:
        return _receipt(
            "SELL_BLOCKED",
            readiness.block_reasons[0] if readiness.block_reasons else "blocked",
            block_reasons=readiness.block_reasons,
        )
    # 게이트는 통과 — 이제 정확한 확인 문구가 있어야만 제출 시도.
    if confirmation != CONFIRM_REAL_SELL_PHRASE:
        reason = f"확인 문구 누락/불일치 — 정확히 {CONFIRM_REAL_SELL_PHRASE} 필요"
        return _receipt("SELL_BLOCKED", reason, block_reasons=[reason])
    # 확인 통과 → executor 제출. 가짜 흔적(True/1)을 넘겨도 model_post_init이 test/proof면 0으로 강제.
    if isinstance(ex, MockSellExecutor):
        result = ex.submit_limit_sell(
            symbol=intent.symbol, quantity=intent.planned_quantity or 0.0,
            limit_price=intent.planned_limit_price or 0.0,
        )
        return _receipt(
            "SELL_SUBMITTED",
            "Mock sell executor (test only) — no real sell order submitted",
            broker_order_id=result.get("broker_order_id"),
            real_sell_order_placed=True, real_sell_orders_placed=1,
        )
    try:
        result = ex.submit_limit_sell(
            symbol=intent.symbol, quantity=intent.planned_quantity or 0.0,
            limit_price=intent.planned_limit_price or 0.0,
        )
    except RealSellExecutionDisabled as exc:
        reason = f"실 매도 경로 미결선 (fail-closed): {exc}"
        return _receipt("SELL_BLOCKED", reason, block_reasons=[reason])
    # 미래 실 결선 시 도달. production·실 시장시간만 실 카운터 보존(model_post_init이 강제).
    return _receipt(
        "SELL_SUBMITTED", "Real sell order submitted",
        broker_order_id=result.get("broker_order_id"),
        real_sell_order_placed=True, real_sell_orders_placed=1,
    )


def process_sell_submit(
    intent: OrderIntent,
    *,
    confirmation: str | None,
    settings: Settings | None = None,
    executor: SellExecutor | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
) -> RealSellExecutionReceipt:
    """확인 게이트를 통과한 매도 제출을 시도해 영수증을 append한다.

    프로덕션에선 실 executor가 항상 disabled → SELL_BLOCKED(fail-closed). 테스트만 MockSellExecutor로
    SELL_SUBMITTED(environment=test)를 만들 수 있다.
    """
    settings = settings or Settings()
    arm = read_sell_arm(reports_dir=reports_dir)
    snapshot = latest_snapshot(reports_dir=reports_dir)
    flags = read_control_flags(reports_dir=reports_dir)
    readiness = evaluate_sell_readiness(
        intent, settings=settings, arm=arm, snapshot=snapshot,
        sold_keys=sold_keys(reports_dir=reports_dir), control_flags=flags, now=now, market_open=market_open,
    )
    mhs: Literal["real", "mocked"] = "mocked" if market_open is not None else "real"
    receipt = build_sell_submit_receipt(
        intent, readiness, confirmation=confirmation, executor=executor, market_hours_source=mhs,
    )
    return append_sell_receipt(receipt, reports_dir=reports_dir)


def real_sell_orders_placed_count(*, reports_dir: Path | None = None) -> int:
    """실제 제출된(production·실 시장시간·non-proof SELL_SUBMITTED) 매도 수. 이 task에선 항상 0."""
    return sum(
        1 for r in load_sell_receipts(limit=500, reports_dir=reports_dir)
        if r.decision == "SELL_SUBMITTED"
        and r.environment == "production"
        and r.market_hours_source == "real"
        and not r.is_proof_run
    )


# --- 읽기 전용 상태 요약(API/UI) ---
class SellExecutionStatus(BaseModel):
    allow_real_sell_orders: bool
    # 실 매도 제출 경로가 결선돼 있음(단, 실 executor는 항상 disabled → 프로덕션 제출 불가).
    sell_submit_wiring: bool = True
    confirmation_required: bool = True
    confirmation_phrase: str = CONFIRM_REAL_SELL_PHRASE
    sell_arm_status: str
    sell_arm_expires_at: str | None = None
    sellable_positions: list[dict] = Field(default_factory=list)
    latest_decision: str | None = None
    latest_block_reason: str | None = None
    latest_environment: str | None = None
    real_sell_orders_placed: int = 0


def sell_execution_status(*, settings: Settings | None = None, reports_dir: Path | None = None) -> SellExecutionStatus:
    """매도 실행 준비 상태 요약(읽기 전용 — MCP/주문 없음)."""
    settings = settings or Settings()
    arm = read_sell_arm(reports_dir=reports_dir)
    snap = latest_snapshot(reports_dir=reports_dir)
    prod = latest_production_sell_receipt(reports_dir=reports_dir)
    sellable: list[dict] = []
    if snap is not None:
        for p in snap.positions:
            if not isinstance(p, dict):
                continue
            avail = p.get("shares_available_for_sells")
            sellable.append({
                "symbol": p.get("symbol"),
                "quantity": p.get("quantity"),
                "shares_available_for_sells": avail if avail is not None else p.get("quantity"),
            })
    return SellExecutionStatus(
        allow_real_sell_orders=settings.allow_real_sell_orders,
        sell_submit_wiring=True,
        confirmation_required=True,
        confirmation_phrase=CONFIRM_REAL_SELL_PHRASE,
        sell_arm_status=sell_arm_state(arm),
        sell_arm_expires_at=arm.expires_at if arm else None,
        sellable_positions=sellable,
        latest_decision=prod.decision if prod else None,
        latest_block_reason=(prod.reason if prod and prod.decision == "SELL_BLOCKED" else None),
        latest_environment=prod.environment if prod else None,
        real_sell_orders_placed=real_sell_orders_placed_count(reports_dir=reports_dir),
    )
