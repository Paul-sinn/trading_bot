"""ExecutionGate (dry-run) + OrderIntent — 주문 안전성 검증(주문 실행 없음).

CRITICAL: 브로커/Robinhood 호출 없음, 실주문 없음, 실자금 이동 없음. ExecutionGate는 승인된
candidate가 dry-run으로 주문 가능한지 *검증만* 하고, 통과 시 `OrderIntent`(계획서)를 만든다.
OrderIntent는 주문이 아니다 — `real_orders_placed=0`, `broker_order_id=None`, status DRY_RUN_INTENT_ONLY.

spec: specs/live_decision_pipeline.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale
from backend.app.services.llm_review import ReviewResult

ExecutionGateStatus = Literal["accepted_dry_run", "rejected"]
INTENT_STATUS = "DRY_RUN_INTENT_ONLY"
_DRY_RUN_MODES = ("report_only",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ExecutionCaps:
    """ExecutionGate dry-run 한도(실주문 없음 — 계획 수치 검증용)."""

    max_notional_per_order_usd: float
    max_daily_order_intents: int
    max_total_intended_exposure_usd: float


class OrderIntent(BaseModel):
    """dry-run 주문 계획서. **주문 아님** — real_orders_placed=0, broker_order_id None."""

    timestamp: str
    session_id: str | None
    trading_mode: str
    strategy_id: str
    symbol: str
    side: str = "BUY"
    scan_event_key: str
    mock_llm_decision: str
    mock_llm_confidence: float
    mock_llm_reason: str
    execution_gate_status: ExecutionGateStatus
    rejection_reasons: list[str] = []
    planned_order_type: str = "limit"
    planned_limit_price: float | None = None
    planned_notional_usd: float | None = None
    planned_quantity: float | None = None
    real_orders_placed: int = 0
    broker_order_id: None = None
    status: str = INTENT_STATUS
    warnings: list[str] = []


class ExecutionGateResult(BaseModel):
    status: ExecutionGateStatus
    rejection_reasons: list[str] = []
    warnings: list[str] = []


class ExecutionGate:
    """승인 candidate의 dry-run 주문 안전성 검증(주문 실행 없음)."""

    def evaluate(
        self,
        *,
        symbol: str,
        price: float | None,
        review: ReviewResult,
        source_status: str,
        scan_event_key: str,
        session_id: str | None,
        trading_mode: str,
        strategy_id: str,
        universe: tuple[str, ...],
        existing_intent_keys: set[str],
        daily_intent_count: int,
        total_intended_exposure_usd: float,
        caps: ExecutionCaps,
        automation_running: bool,
        emergency_halt: bool,
        broker_snapshot: BrokerSnapshot | None = None,
        snapshot_max_age_seconds: int = 3600,
        reject_on_stale_snapshot: bool = False,
    ) -> tuple[ExecutionGateResult, OrderIntent]:
        reasons: list[str] = []
        warnings: list[str] = []

        # --- 안전/상태 게이트 ---
        if emergency_halt:
            reasons.append("emergency_halt 활성")
        if not automation_running:
            reasons.append("automation_running=false")
        if trading_mode not in _DRY_RUN_MODES:
            reasons.append(f"trading_mode 비호환: {trading_mode}")
        if source_status != "BUY_CANDIDATE":
            reasons.append(f"source decision != BUY_CANDIDATE: {source_status}")
        if review.decision != "approve":
            reasons.append(f"mock LLM 승인 아님: {review.decision}")
        if symbol not in universe:
            reasons.append("심볼이 베이스라인 유니버스 밖")
        if scan_event_key in existing_intent_keys:
            reasons.append("중복 OrderIntent")

        # --- 계획 수치 산출(주문 아님 — 서술적 계획값) ---
        # 노셔널은 cap 이하만(override는 더 낮을 때만 적용 — 리스크 상향 금지).
        cap = caps.max_notional_per_order_usd
        override = review.max_notional_override_usd
        planned_notional = cap if override is None else min(cap, override)
        planned_limit_price = price
        planned_quantity: float | None = None
        if price is None or not math.isfinite(price) or price <= 0:
            reasons.append("limit price 유한/양수 아님")
        else:
            planned_quantity = planned_notional / price
            if not math.isfinite(planned_quantity) or planned_quantity <= 0:
                reasons.append("planned_quantity 유한/양수 아님")

        # --- 한도 게이트 ---
        if planned_notional > cap:
            reasons.append(f"MAX_NOTIONAL_PER_ORDER 초과: {planned_notional} > {cap}")
        if daily_intent_count >= caps.max_daily_order_intents:
            reasons.append("MAX_DAILY_ORDER_INTENTS 초과")
        if total_intended_exposure_usd + planned_notional > caps.max_total_intended_exposure_usd:
            reasons.append("MAX_TOTAL_INTENDED_EXPOSURE 초과")

        # --- 브로커 스냅샷 게이트(읽기 전용 — 브로커 호출 없음, 주문 없음) ---
        # 스냅샷이 없으면 경고만(report_only 기본). 있으면 buying_power/중복주문/신선도 검증.
        if broker_snapshot is None:
            warnings.append("브로커 스냅샷 없음 — 잔고/중복주문 미검증")
        else:
            if is_stale(broker_snapshot, max_age_seconds=snapshot_max_age_seconds):
                if reject_on_stale_snapshot:
                    reasons.append("브로커 스냅샷 stale")
                else:
                    warnings.append("브로커 스냅샷 stale (경고)")
            bp = broker_snapshot.buying_power
            if bp is not None and planned_notional > bp:
                reasons.append(f"BUYING_POWER 부족: {planned_notional} > {bp}")
            if _has_open_buy(broker_snapshot, symbol):
                reasons.append(f"중복 미체결 매수 주문 존재: {symbol}")

        status: ExecutionGateStatus = "accepted_dry_run" if not reasons else "rejected"
        intent = OrderIntent(
            timestamp=_now_iso(),
            session_id=session_id,
            trading_mode=trading_mode,
            strategy_id=strategy_id,
            symbol=symbol,
            scan_event_key=scan_event_key,
            mock_llm_decision=review.decision,
            mock_llm_confidence=review.confidence,
            mock_llm_reason=review.reason,
            execution_gate_status=status,
            rejection_reasons=reasons,
            planned_limit_price=planned_limit_price,
            planned_notional_usd=planned_notional if status == "accepted_dry_run" else None,
            planned_quantity=planned_quantity if status == "accepted_dry_run" else None,
            real_orders_placed=0,
            warnings=warnings,
        )
        return (
            ExecutionGateResult(status=status, rejection_reasons=reasons, warnings=warnings),
            intent,
        )


def _has_open_buy(snapshot: BrokerSnapshot, symbol: str) -> bool:
    """스냅샷의 미체결 주문 중 같은 심볼의 매수 주문이 있으면 True(중복 주문 방지)."""
    for order in snapshot.open_orders:
        if not isinstance(order, dict):
            continue
        side = str(order.get("side", "")).lower()
        if order.get("symbol") == symbol and side == "buy":
            return True
    return False
