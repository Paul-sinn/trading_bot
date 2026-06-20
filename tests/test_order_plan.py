"""order_plan 테스트 (spec: specs/order_plan.md).

사전 주문계획(한정매수 + 진입 전 청산 첨부, 하드 결정론 청산). 측정 전용 — 체결/매매/veto 불변.
can_trade_live=False, real_orders=0. 네트워크 없음.
"""

import pytest

from agents.order_plan import (
    LEVERAGED_SHADOW_PROFILE,
    NORMAL_PROFILE,
    ExitProfile,
    OrderPlan,
    OrderPlanReport,
    build_order_plan,
    compute_order_plan_diagnostics,
    format_order_plan,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _leg(symbol, entry_date, entry_price):
    return TradeLeg(
        symbol=symbol, entry_date=entry_date, exit_date="2025-12-31",
        entry_price=entry_price, exit_price=entry_price * 1.1, qty=1.0,
        pnl=entry_price * 0.1, pnl_pct=0.1, exit_reason="sell",
    )


def _trade_diag(legs):
    return TradeDiagnostics(
        trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
        equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


# --- 단일 plan ---


def test_build_order_plan_normal():
    p = build_order_plan("NVDA", "2025-06-20", 100.0)
    assert isinstance(p, OrderPlan)
    assert p.symbol == "NVDA"
    assert p.entry_date == "2025-06-20"
    assert p.reference_price == 100.0
    assert p.entry_order_type == "limit_buy_shadow"
    assert p.route_type == "normal"
    assert p.can_trade_live is False
    assert p.real_orders_placed == 0


def test_limit_price_caps_slippage():
    p = build_order_plan("NVDA", "d", 200.0, max_slippage_pct=0.005)
    assert p.max_entry_slippage_pct == 0.005
    assert p.suggested_limit_price == pytest.approx(200.0 * 1.005)
    assert p.order_timeout_policy == "cancel_end_of_day"


def test_limit_price_safe_on_bad_reference():
    for bad in (None, 0.0, -5.0):
        p = build_order_plan("X", "d", bad)
        assert p.suggested_limit_price is None     # 예외 없이 안전
        assert p.can_trade_live is False


def test_exit_profile_attached_before_entry():
    p = build_order_plan("NVDA", "d", 100.0)
    prof = p.attached_exit_profile
    assert isinstance(prof, ExitProfile)
    assert prof.stop_loss_pct == 0.15
    assert prof.trailing_stop_pct == 0.20
    assert prof.max_holding_days == 60


def test_no_fixed_full_take_profit_enforced():
    p = build_order_plan("NVDA", "d", 100.0)
    # 고정 전량 익절 필드가 없고, partial은 미강제(None 기본).
    assert p.attached_exit_profile.partial_take_profit is None
    assert not hasattr(p.attached_exit_profile, "full_take_profit_pct")


def test_leveraged_shadow_tighter_profile():
    p = build_order_plan("NVDA", "d", 100.0, route="leveraged_shadow")
    assert p.route_type == "leveraged_shadow"
    prof = p.attached_exit_profile
    assert prof.stop_loss_pct == 0.07
    assert prof.trailing_stop_pct == 0.10
    assert prof.max_holding_days == 10
    assert prof.time_cut_days == 3
    assert p.can_trade_live is False           # 그림자 — 실 매매 불가


def test_no_trade_route():
    p = build_order_plan("NVDA", "d", 100.0, route="no_trade")
    assert p.route_type == "no_trade"
    assert p.attached_exit_profile is None
    assert p.suggested_limit_price is None
    assert p.real_orders_placed == 0


def test_profiles_constants():
    assert NORMAL_PROFILE.stop_loss_pct == 0.15
    assert LEVERAGED_SHADOW_PROFILE.max_holding_days == 10


# --- 매트릭스(trade_diag) ---


def test_order_plan_renders_for_trades():
    legs = [_leg("NVDA", "2025-06-20", 120.0), _leg("AMD", "2025-07-01", 180.0)]
    rep = compute_order_plan_diagnostics(_trade_diag(legs))
    assert isinstance(rep, OrderPlanReport)
    assert len(rep.plans) == 2
    syms = {p.symbol for p in rep.plans}
    assert syms == {"NVDA", "AMD"}
    for p in rep.plans:
        assert p.entry_order_type == "limit_buy_shadow"
        assert p.attached_exit_profile is not None        # 진입 전 청산 첨부
        assert p.suggested_limit_price is not None
        assert p.route_type == "normal"
    assert rep.can_trade_live is False
    assert rep.real_orders_placed == 0


def test_custom_profile_used():
    legs = [_leg("NVDA", "d", 100.0)]
    rep = compute_order_plan_diagnostics(_trade_diag(legs), profile=LEVERAGED_SHADOW_PROFILE)
    assert rep.plans[0].attached_exit_profile.stop_loss_pct == 0.07


def test_dedupe_same_entry():
    # 같은 진입(symbol,date,price)이 FIFO로 2개 leg로 쪼개져도 주문계획은 1건.
    legs = [_leg("NVDA", "2025-06-20", 120.0), _leg("NVDA", "2025-06-20", 120.0)]
    rep = compute_order_plan_diagnostics(_trade_diag(legs))
    assert len(rep.plans) == 1


def test_diagnostics_do_not_change_trades():
    legs = [_leg("NVDA", "2025-06-20", 120.0)]
    td = _trade_diag(legs)
    before = td.trades
    compute_order_plan_diagnostics(td)
    assert td.trades == before                 # 매매/체결/veto 불변
    assert all(p.real_orders_placed == 0 for p in compute_order_plan_diagnostics(td).plans)


def test_format_contains_sections():
    legs = [_leg("NVDA", "2025-06-20", 120.0)]
    rep = compute_order_plan_diagnostics(_trade_diag(legs))
    text = format_order_plan(rep)
    assert "Order Plan" in text
    assert "limit_buy_shadow" in text
    assert "can_trade_live" in text.lower() or "can_trade_live = false" in text.lower()
    assert "real_orders_placed : 0" in text
