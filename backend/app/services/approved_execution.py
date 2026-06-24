"""Discord 승인 실행 워커 v1 — 승인된 요청을 받아 모든 게이트 재확인 후 1건만 실행 준비/실행.

흐름: 최신 APPROVED 결정 → 매칭 승인 요청 → 모든 리스크 게이트 **재확인**(승인은 게이트를 우회하지 않음)
→ dry-run이면 APPROVED_READY_DRY_RUN(제출 없음), execute-real이면 정확히 1건 제출.

CRITICAL 안전 불변식(이 task):
- 구현/테스트 중 실주문 없음. 실 executor(`RealRobinhoodOrderExecutor`)는 항상 disabled → 프로덕션
  execute-real은 fail-closed(BLOCKED). 테스트만 `MockOrderExecutor`로 REAL_SUBMITTED(environment=test).
- 실 매도/취소/review/옵션 없음. BUY 한정(v1). 재시도/2차 주문 없음.
- Robinhood write MCP 도구를 import/호출하지 않는다. 실 흔적은 production·real·non-proof REAL_SUBMITTED만.

spec: specs/real_order_v1_checklist.md §13
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from backend.app.core.config import Settings
from backend.app.services.approval_gate import approval_gate_for_intent
from backend.app.services.approval_store import ApprovalRequest, get_request, load_decisions
from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale, latest_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_executor import (
    MockOrderExecutor,
    OrderExecutor,
    RealExecutionDisabled,
    RealExecutionReceipt,
    RealRobinhoodOrderExecutor,
    _has_open_buy,
    append_execution_receipt,
    daily_real_order_count,
    executed_keys,
    is_market_open,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _synth_intent(request: ApprovalRequest) -> OrderIntent:
    """승인 요청으로부터 게이트 재확인용 합성 OrderIntent를 만든다(주문 아님)."""
    return OrderIntent(
        timestamp=request.created_at, session_id=None, trading_mode="report_only",
        strategy_id=request.strategy_id, symbol=request.symbol, side=request.side,
        scan_event_key=request.source_intent_id, mock_llm_decision="approve",
        mock_llm_confidence=1.0, mock_llm_reason="discord approved",
        execution_gate_status="accepted_dry_run", planned_order_type=request.order_type,
        planned_limit_price=request.limit_price, planned_notional_usd=request.notional,
        planned_quantity=request.quantity,
    )


def evaluate_approved_gates(
    request: ApprovalRequest,
    intent: OrderIntent,
    *,
    settings: Settings,
    snapshot: BrokerSnapshot | None,
    reports_dir: Path | None,
    now: datetime,
    market_open: bool | None,
) -> list[str]:
    """승인 요청에 대한 모든 리스크 게이트를 재확인한다(실 write 없음). 위반 사유를 모은다."""
    reasons: list[str] = []
    account_last4 = snapshot.account_last4 if snapshot is not None else None
    daily = daily_real_order_count(reports_dir=reports_dir, now=now)
    keys = executed_keys(reports_dir=reports_dir)

    # 승인 게이트(상태 APPROVED·허용 사용자·만료·preview_hash·idempotency·notional·daily 재확인).
    gate = approval_gate_for_intent(
        intent, type=request.type, settings=settings, account_last4=account_last4,
        reports_dir=reports_dir, now=now, daily_real_count=daily, executed_keys=keys,
    )
    reasons += gate.block_reasons

    # 출처: 전략/라이브스캔 intent만(테스트성 차단).
    if intent.strategy_id != settings.live_strategy_id and not settings.test_only_intent_real_order_allowed:
        reasons.append("test-only/non-strategy intent")
    if intent.side != "BUY":
        reasons.append("BUY only (v1)")
    if getattr(intent, "asset_type", "equity") != "equity":
        reasons.append("옵션 미허용 (equity only)")
    if intent.planned_order_type not in ("limit", "market"):
        reasons.append(f"limit/market only: {intent.planned_order_type}")

    if snapshot is None:
        reasons.append("broker snapshot 없음")
    else:
        if settings.agentic_account_only and (not account_last4 or account_last4 == "••••"):
            reasons.append("AGENTIC_ACCOUNT_ONLY: 스냅샷 계정 미상")
        if settings.require_fresh_broker_snapshot_for_real_order and is_stale(
            snapshot, max_age_seconds=settings.broker_snapshot_max_age_seconds, now=now
        ):
            reasons.append("broker snapshot stale")
        if _has_open_buy(snapshot, intent.symbol):
            reasons.append(f"중복 미체결 매수 주문 존재: {intent.symbol}")
        bp = snapshot.buying_power
        if intent.planned_notional_usd is not None and bp is not None and intent.planned_notional_usd > bp:
            reasons.append(f"buying_power 부족: {intent.planned_notional_usd} > {bp}")

    if intent.planned_notional_usd is None:
        reasons.append("notional 없음")
    elif intent.planned_notional_usd > settings.max_notional_per_real_order_usd:
        reasons.append(f"notional > MAX_NOTIONAL_PER_REAL_ORDER: {intent.planned_notional_usd} > {settings.max_notional_per_real_order_usd}")

    mo = is_market_open(now) if market_open is None else market_open
    if settings.require_market_hours_for_real_order and not mo:
        reasons.append("장시간 아님")
    return reasons


def _build_receipt(
    request: ApprovalRequest | None,
    reasons: list[str],
    *,
    executor: OrderExecutor | None,
    execute_real: bool,
    market_hours_source: Literal["real", "mocked"],
    no_approval_reason: str | None = None,
) -> RealExecutionReceipt:
    proof = market_hours_source == "mocked" or isinstance(executor, MockOrderExecutor)
    environment: Literal["production", "test"] = "test" if proof else "production"
    r = request

    def _r(decision, reason, *, broker_order_id=None, real_order_placed=False, real_orders_placed=0):
        return RealExecutionReceipt(
            intent_id=(r.source_intent_id if r else "none"),
            idempotency_key=(r.source_intent_id if r else "none"),
            symbol=(r.symbol if r else "-"), side=(r.side if r else "BUY"),
            quantity=(r.quantity if r else None), dollar_amount=(r.dollar_amount if r else None),
            limit_price=(r.limit_price if r else None), notional=(r.notional if r else None),
            order_type=(r.order_type if r else None), approval_id=(r.approval_id if r else None),
            source_intent_id=(r.source_intent_id if r else None), strategy_id=(r.strategy_id if r else None),
            executor=(executor.name if executor else "real_robinhood"),
            environment=environment, market_hours_source=market_hours_source, is_proof_run=proof,
            decision=decision, reason=reason, block_reasons=reasons,
            broker_order_id=broker_order_id, real_order_placed=real_order_placed,
            real_orders_placed=real_orders_placed,
        )

    if no_approval_reason is not None:
        return _r("BLOCKED", no_approval_reason)
    if reasons:
        return _r("BLOCKED", reasons[0])
    if not execute_real:
        return _r("APPROVED_READY_DRY_RUN", "모든 게이트 통과 — dry-run, 주문 제출 없음")
    # execute-real: executor 제출 시도(실 executor는 disabled → fail-closed).
    assert r is not None
    ex = executor or RealRobinhoodOrderExecutor()
    if isinstance(ex, MockOrderExecutor):
        res = ex.submit_limit_buy(symbol=r.symbol, quantity=r.quantity or 0.0, limit_price=r.limit_price or 0.0)
        return _r("REAL_SUBMITTED", "Mock executor (test only) — no real order submitted",
                  broker_order_id=res.get("broker_order_id"), real_order_placed=True, real_orders_placed=1)
    try:
        res = ex.submit_limit_buy(symbol=r.symbol, quantity=r.quantity or 0.0, limit_price=r.limit_price or 0.0)
    except RealExecutionDisabled as exc:
        return _r("BLOCKED", f"실 실행 경로 미결선 (fail-closed): {exc}")
    return _r("REAL_SUBMITTED", "Real order submitted", broker_order_id=res.get("broker_order_id"),
              real_order_placed=True, real_orders_placed=1)


def process_approved_execution(
    *,
    settings: Settings | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
    executor: OrderExecutor | None = None,
    execute_real: bool = False,
) -> RealExecutionReceipt:
    """최신 APPROVED 승인을 처리해 영수증을 기록한다. dry-run 기본(제출 없음)."""
    settings = settings or Settings()
    now = now or _now()
    mhs: Literal["real", "mocked"] = "mocked" if market_open is not None else "real"
    snapshot = latest_snapshot(reports_dir=reports_dir)

    decisions = load_decisions(reports_dir=reports_dir)
    last_valid = next((d for d in reversed(decisions) if d.valid), None)
    if last_valid is None or last_valid.decision != "APPROVE":
        rcpt = _build_receipt(None, [], executor=executor, execute_real=execute_real,
                              market_hours_source=mhs, no_approval_reason="최신 승인(APPROVE) 결정 없음")
        return append_execution_receipt(rcpt, reports_dir=reports_dir)

    request = get_request(last_valid.approval_id, reports_dir=reports_dir)
    if request is None:
        rcpt = _build_receipt(None, [], executor=executor, execute_real=execute_real,
                              market_hours_source=mhs, no_approval_reason="승인 요청을 찾을 수 없음")
        return append_execution_receipt(rcpt, reports_dir=reports_dir)

    intent = _synth_intent(request)
    reasons = evaluate_approved_gates(
        request, intent, settings=settings, snapshot=snapshot, reports_dir=reports_dir,
        now=now, market_open=market_open,
    )
    if execute_real and not settings.enable_real_order_execution:
        reasons.append("ENABLE_REAL_ORDER_EXECUTION=false")
    rcpt = _build_receipt(request, reasons, executor=executor, execute_real=execute_real,
                          market_hours_source=mhs)
    return append_execution_receipt(rcpt, reports_dir=reports_dir)
