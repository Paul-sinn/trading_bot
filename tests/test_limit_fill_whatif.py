"""limit_fill_whatif 테스트 (spec: specs/limit_fill_whatif.md).

한정매수 계획이 일봉 OHLC로 체결됐을지 추정(what-if 전용). 실 체결/포트폴리오 불변. OHLC 결측은 unknown.
real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd
import pytest

from agents.limit_fill_whatif import (
    LimitFillReport,
    compute_limit_fill_whatif,
    format_limit_fill_whatif,
)
from agents.order_plan import OrderPlanReport, build_order_plan
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _bar(date, o, h, l, c):
    return pd.DataFrame(
        {"open": [o], "high": [h], "low": [l], "close": [c]},
        index=pd.DatetimeIndex([pd.Timestamp(date)]),
    )


def _plan(symbol, date, ref, *, slip=0.0):
    return build_order_plan(symbol, date, ref, max_slippage_pct=slip)


def _report(plans):
    return OrderPlanReport(plans=tuple(plans))


def _row(report, symbol):
    return next(r for r in report.rows if r.symbol == symbol)


# --- 체결 모델 ---


def test_fills_at_open_when_open_below_limit():
    plans = _report([_plan("X", "2025-06-20", 100.0)])      # limit 100
    price = {"X": _bar("2025-06-20", o=99.0, h=101.0, l=98.0, c=100.5)}
    rep = compute_limit_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.status == "filled"
    assert r.fill_at == "open"
    assert r.shadow_fill_price == 99.0


def test_fills_at_limit_when_low_touches():
    plans = _report([_plan("X", "2025-06-20", 100.0)])      # limit 100
    price = {"X": _bar("2025-06-20", o=101.0, h=102.0, l=99.5, c=101.5)}
    rep = compute_limit_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.status == "filled"
    assert r.fill_at == "limit"
    assert r.shadow_fill_price == 100.0


def test_missed_when_price_never_reaches_limit():
    plans = _report([_plan("X", "2025-06-20", 100.0)])      # limit 100
    price = {"X": _bar("2025-06-20", o=105.0, h=106.0, l=104.0, c=105.5)}
    rep = compute_limit_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.status == "missed"
    assert r.shadow_fill_price is None


def test_missing_ohlc_marks_unknown():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bar("2025-01-02", o=99.0, h=101.0, l=98.0, c=100.0)}   # 다른 날짜
    rep = compute_limit_fill_whatif(plans, price)
    assert _row(rep, "X").status == "unknown"
    # 심볼 자체가 없을 때도 unknown.
    rep2 = compute_limit_fill_whatif(plans, {})
    assert _row(rep2, "X").status == "unknown"


def test_slippage_limit_distance():
    plans = _report([_plan("X", "2025-06-20", 200.0, slip=0.005)])   # limit 201
    price = {"X": _bar("2025-06-20", o=199.0, h=202.0, l=198.0, c=200.5)}
    rep = compute_limit_fill_whatif(plans, price)
    assert _row(rep, "X").limit_price == pytest.approx(201.0)
    assert rep.avg_limit_distance == pytest.approx(0.005)


# --- 집계 / 경고 ---


def _td(pnls):
    legs = [
        TradeLeg(symbol=s, entry_date=d, exit_date="2025-12-31", entry_price=100.0,
                 exit_price=100.0 + p, qty=1.0, pnl=p, pnl_pct=p / 100.0, exit_reason="sell")
        for s, d, p in pnls
    ]
    return TradeDiagnostics(
        trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
        equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


def test_aggregate_fill_rate_and_missed():
    plans = _report([
        _plan("A", "2025-06-20", 100.0), _plan("B", "2025-06-20", 100.0),
        _plan("C", "2025-06-20", 100.0),
    ])
    price = {
        "A": _bar("2025-06-20", 99.0, 101.0, 98.0, 100.0),   # filled (open)
        "B": _bar("2025-06-20", 105.0, 106.0, 104.0, 105.0),  # missed
        "C": _bar("2025-01-01", 99.0, 101.0, 98.0, 100.0),    # unknown
    }
    rep = compute_limit_fill_whatif(plans, price)
    assert isinstance(rep, LimitFillReport)
    assert rep.total_planned == 3
    assert rep.filled_count == 1
    assert rep.missed_count == 1
    assert rep.unknown_count == 1
    assert rep.fill_rate == pytest.approx(0.5)        # filled / (filled+missed)
    assert rep.real_orders_placed == 0


def test_missed_profitable_warning_and_best_worst():
    plans = _report([_plan("A", "2025-06-20", 100.0), _plan("B", "2025-06-20", 100.0)])
    price = {
        "A": _bar("2025-06-20", 99.0, 101.0, 98.0, 100.0),    # filled
        "B": _bar("2025-06-20", 110.0, 111.0, 109.0, 110.0),  # missed (큰 수익 트레이드였음)
    }
    td = _td([("A", "2025-06-20", 10.0), ("B", "2025-06-20", 200.0)])
    rep = compute_limit_fill_whatif(plans, price, trade_diag=td)
    assert any("미체결" in w or "누락" in w for w in rep.warnings)
    assert rep.worst_missed is not None and rep.worst_missed.symbol == "B"
    assert rep.best_missed.symbol == "B"             # 미체결 중 PnL 최고


def test_loose_limit_warning_when_all_fill():
    plans = _report([_plan(s, "2025-06-20", 100.0) for s in ("A", "B", "C", "D")])
    price = {s: _bar("2025-06-20", 99.0, 101.0, 98.0, 100.0) for s in ("A", "B", "C", "D")}
    rep = compute_limit_fill_whatif(plans, price)
    assert rep.fill_rate == 1.0
    assert any("느슨" in w or "loose" in w.lower() for w in rep.warnings)


# --- 불변 ---


def test_inputs_not_mutated():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bar("2025-06-20", 99.0, 101.0, 98.0, 100.0)}
    before = price["X"].copy()
    plans_before = plans.plans
    compute_limit_fill_whatif(plans, price)
    pd.testing.assert_frame_equal(price["X"], before)
    assert plans.plans == plans_before


def test_format_contains_sections():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bar("2025-06-20", 99.0, 101.0, 98.0, 100.0)}
    rep = compute_limit_fill_whatif(plans, price)
    text = format_limit_fill_whatif(rep)
    assert "Limit" in text and "Fill" in text
    assert "fill_rate" in text.lower()
    assert "real_orders_placed : 0" in text
