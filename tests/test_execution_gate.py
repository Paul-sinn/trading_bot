"""ExecutionGate dry-run 테스트 (spec: specs/live_decision_pipeline.md).

브로커/주문 없음. 유효 심볼·중복·노셔널/일일/총노출 한도·emergency_halt/automation 검증.
accepted_dry_run은 real_orders_placed=0·broker None·DRY_RUN_INTENT_ONLY OrderIntent를 만든다.
"""

from __future__ import annotations

from backend.app.services.execution_gate import ExecutionCaps, ExecutionGate
from backend.app.services.live_scan import LIVE_BASELINE_UNIVERSE
from backend.app.services.llm_review import ReviewResult

_CAPS = ExecutionCaps(
    max_notional_per_order_usd=1000.0,
    max_daily_order_intents=20,
    max_total_intended_exposure_usd=5000.0,
)


def _approve(symbol="NVDA"):
    return ReviewResult(symbol=symbol, decision="approve", confidence=0.9, reason="ok")


def _evaluate(gate, **overrides):
    kwargs = dict(
        symbol="NVDA",
        price=100.0,
        review=_approve(),
        source_status="BUY_CANDIDATE",
        scan_event_key="s1|NVDA|2026-06-22|strat",
        session_id="s1",
        trading_mode="report_only",
        strategy_id="strat",
        universe=LIVE_BASELINE_UNIVERSE,
        existing_intent_keys=set(),
        daily_intent_count=0,
        total_intended_exposure_usd=0.0,
        caps=_CAPS,
        automation_running=True,
        emergency_halt=False,
    )
    kwargs.update(overrides)
    return gate.evaluate(**kwargs)


def test_accepted_dry_run_produces_intent():
    result, intent = _evaluate(ExecutionGate())
    assert result.status == "accepted_dry_run"
    assert intent.status == "DRY_RUN_INTENT_ONLY"
    assert intent.real_orders_placed == 0
    assert intent.broker_order_id is None
    assert intent.planned_limit_price == 100.0
    assert intent.planned_notional_usd == 1000.0
    assert intent.planned_quantity == 10.0
    assert intent.side == "BUY"


def test_rejects_invalid_symbol():
    result, intent = _evaluate(ExecutionGate(), symbol="FAKE", universe=LIVE_BASELINE_UNIVERSE)
    assert result.status == "rejected"
    assert any("유니버스" in r for r in result.rejection_reasons)
    assert intent.real_orders_placed == 0


def test_rejects_duplicate_intent():
    key = "s1|NVDA|2026-06-22|strat"
    result, _ = _evaluate(ExecutionGate(), scan_event_key=key, existing_intent_keys={key})
    assert result.status == "rejected"
    assert any("중복" in r for r in result.rejection_reasons)


def test_enforces_max_notional_per_order():
    caps = ExecutionCaps(max_notional_per_order_usd=50.0, max_daily_order_intents=20,
                         max_total_intended_exposure_usd=5000.0)
    # override가 cap보다 높아도 cap으로 클램프(리스크 상향 불가) → 한도 위반 없음, 노셔널=cap.
    review = ReviewResult(symbol="NVDA", decision="approve", confidence=0.9, reason="ok",
                          max_notional_override_usd=999999.0)
    result, intent = _evaluate(ExecutionGate(), caps=caps, review=review)
    assert result.status == "accepted_dry_run"
    assert intent.planned_notional_usd == 50.0  # cap으로 제한(상향 불가)


def test_enforces_max_daily_order_intents():
    result, _ = _evaluate(ExecutionGate(), daily_intent_count=20)
    assert result.status == "rejected"
    assert any("MAX_DAILY_ORDER_INTENTS" in r for r in result.rejection_reasons)


def test_enforces_max_total_intended_exposure():
    result, _ = _evaluate(ExecutionGate(), total_intended_exposure_usd=4500.0)
    assert result.status == "rejected"
    assert any("MAX_TOTAL_INTENDED_EXPOSURE" in r for r in result.rejection_reasons)


def test_rejects_when_emergency_halt():
    result, _ = _evaluate(ExecutionGate(), emergency_halt=True)
    assert result.status == "rejected"


def test_rejects_when_not_automation_running():
    result, _ = _evaluate(ExecutionGate(), automation_running=False)
    assert result.status == "rejected"


def test_rejects_non_approve_review():
    veto = ReviewResult(symbol="NVDA", decision="veto", confidence=0.1, reason="no")
    result, _ = _evaluate(ExecutionGate(), review=veto)
    assert result.status == "rejected"


def test_rejects_invalid_price():
    result, _ = _evaluate(ExecutionGate(), price=0.0)
    assert result.status == "rejected"
    assert any("limit price" in r for r in result.rejection_reasons)
