"""sim_execution 테스트 (spec: specs/sim_execution.md).

핵심: 시뮬 주문은 RiskGate(전역 게이트 + per-candidate hard-veto)를 통과해야만 생긴다. veto면 시뮬
주문 없음. 우회 불가. 실주문(real_orders_placed)은 항상 0. 네트워크/브로커 없음.
"""

import pytest

from agents.decision import Decision
from agents.sim_execution import (
    SimExecutionResult,
    SimulatedExecutor,
    SimulatedOrder,
)
from algorithms.policy import RiskMode, TierEntry, UniversePolicy, VetoInput
from algorithms.regime import Regime


def _mode_b() -> RiskMode:
    return RiskMode("B", 0.07, ("0", "1", "2", "3", "4A", "4B"), False, (), True)


def _universe() -> UniversePolicy:
    return UniversePolicy(
        entries=(
            TierEntry("NVDA", "1", ("1",), "approved", True, False),
            TierEntry("SMCI", "2", ("2",), "needs_review", True, False),
        )
    )


def _clean_input(symbol="NVDA", **ov) -> VetoInput:
    base = dict(
        symbol=symbol, mode=_mode_b(), universe=_universe(),
        per_trade_risk_pct=0.04, position_weight=0.5, stop_loss_pct=0.08,
        regime=Regime.NORMAL_BULL, has_stop_loss=True, position_size_ok=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return VetoInput(**base)


_PASS_GATE = lambda: (True, "ok")
_BLOCK_GATE = lambda: (False, "kill-switch on")


# --- simulated order only after RiskGate pass ---


def test_simulated_order_created_on_full_pass():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(), Decision.BUY, 10)
    assert res.created is True
    assert isinstance(res.order, SimulatedOrder)
    assert res.order.symbol == "NVDA" and res.order.quantity == 10
    assert len(ex.simulated_orders) == 1


# --- no simulated order on veto ---


def test_no_simulated_order_when_vetoed():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(liquidity_ok=False), Decision.BUY, 10)
    assert res.created is False
    assert res.order is None
    assert ex.simulated_orders == ()
    assert any("liquidity" in r for r in res.veto.reasons)


def test_no_simulated_order_for_needs_review_without_override():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(symbol="SMCI"), Decision.BUY, 10)
    assert res.created is False
    assert ex.simulated_orders == ()


def test_needs_review_with_override_creates_order():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(symbol="SMCI", manual_override=True), Decision.BUY, 5)
    assert res.created is True
    assert len(ex.simulated_orders) == 1


# --- global gate (kill-switch) cannot be bypassed ---


def test_global_gate_block_prevents_order():
    ex = SimulatedExecutor(global_gate=_BLOCK_GATE)
    res = ex.submit(_clean_input(), Decision.BUY, 10)  # veto는 통과하지만 전역 게이트 차단
    assert res.created is False
    assert ex.simulated_orders == ()
    assert "kill-switch" in res.reason


def test_global_gate_exception_fail_closed():
    def boom():
        raise RuntimeError("gate down")

    ex = SimulatedExecutor(global_gate=boom)
    res = ex.submit(_clean_input(), Decision.BUY, 10)
    assert res.created is False
    assert ex.simulated_orders == ()


# --- only BUY entries simulated ---


def test_sell_and_hold_do_not_create_orders():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    for dec in (Decision.SELL, Decision.HOLD):
        res = ex.submit(_clean_input(), dec, 10)
        assert res.created is False
    assert ex.simulated_orders == ()


def test_zero_or_negative_quantity_rejected():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    for q in (0, -5):
        res = ex.submit(_clean_input(), Decision.BUY, q)
        assert res.created is False
    assert ex.simulated_orders == ()


# --- RiskGate cannot be bypassed (구조적) ---


def test_no_public_path_to_add_order_bypassing_gate():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    # 주문 생성의 유일한 경로는 submit(게이트 평가 포함). 직접 추가 공개 API 없음.
    assert not hasattr(ex, "place_order")
    assert not hasattr(ex, "add_order")
    # simulated_orders는 읽기전용 뷰(튜플) — 외부에서 append 불가.
    assert isinstance(ex.simulated_orders, tuple)
    with pytest.raises(AttributeError):
        ex.simulated_orders.append(SimulatedOrder("X", "buy", 1))  # type: ignore[attr-defined]


def test_vetoed_buy_never_creates_order_across_many_veto_causes():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    for field in ("has_stop_loss", "position_size_ok", "data_ok", "ipo_data_ok",
                  "event_risk_checked", "technical_confirmation", "tier_exposure_ok"):
        res = ex.submit(_clean_input(**{field: False}), Decision.BUY, 10)
        assert res.created is False
    res = ex.submit(_clean_input(regime=Regime.BEARISH), Decision.BUY, 10)
    assert res.created is False
    assert ex.simulated_orders == ()


# --- real orders remain zero ---


def test_real_orders_stay_zero_even_with_sim_orders():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    ex.submit(_clean_input("NVDA"), Decision.BUY, 10)
    ex.submit(_clean_input("SMCI", manual_override=True), Decision.BUY, 5)
    assert len(ex.simulated_orders) == 2
    assert ex.real_orders_placed == 0  # 실 브로커 호출 없음 — 항상 0


def test_default_global_gate_is_check_risk_gate(monkeypatch):
    # 기본 전역 게이트는 agents.risk.check_risk_gate(env kill-switch 존중).
    monkeypatch.setenv("RISK_KILL_SWITCH", "on")
    ex = SimulatedExecutor()
    res = ex.submit(_clean_input(), Decision.BUY, 10)
    assert res.created is False  # kill-switch on → 시뮬 주문 없음
    assert ex.real_orders_placed == 0
