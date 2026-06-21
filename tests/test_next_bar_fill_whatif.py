"""next_bar_fill_whatif 테스트 (spec: specs/next_bar_fill_whatif.md).

한정매수를 다음 거래 바에 평가해 lookahead 제거(what-if 전용). 같은-바 vs 다음-바 대조, 다음 바 결측은
unknown. 실 체결 불변. real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd
import pytest

from agents.next_bar_fill_whatif import (
    NextBarFillReport,
    compute_next_bar_fill_whatif,
    format_next_bar_fill_whatif,
)
from agents.order_plan import OrderPlanReport, build_order_plan
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _bars(rows):
    """rows: [(date, o, h, l, c)] → DatetimeIndex OHLC DataFrame."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, *_ in rows])
    return pd.DataFrame(
        {
            "open": [o for _, o, h, l, c in rows],
            "high": [h for _, o, h, l, c in rows],
            "low": [l for _, o, h, l, c in rows],
            "close": [c for _, o, h, l, c in rows],
        },
        index=idx,
    )


def _plan(symbol, date, ref):
    return build_order_plan(symbol, date, ref, max_slippage_pct=0.0)   # limit == ref


def _report(plans):
    return OrderPlanReport(plans=tuple(plans))


def _row(rep, symbol):
    return next(r for r in rep.rows if r.symbol == symbol)


# --- 다음-바 체결 모델 ---


def test_next_bar_open_fill():
    plans = _report([_plan("X", "2025-06-20", 100.0)])      # limit 100
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),          # 시그널 바
        ("2025-06-23", 98.0, 100.5, 97.0, 99.0),            # 다음 바: open 98 <= 100
    ])}
    rep = compute_next_bar_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.next_bar_status == "filled"
    assert r.next_fill_at == "open"
    assert r.next_shadow_fill_price == 98.0
    assert r.next_bar_date == "2025-06-23"


def test_next_bar_intraday_limit_fill():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),
        ("2025-06-23", 101.0, 102.0, 99.5, 101.5),          # open>limit, low 99.5<=100
    ])}
    rep = compute_next_bar_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.next_bar_status == "filled"
    assert r.next_fill_at == "limit"
    assert r.next_shadow_fill_price == 100.0


def test_next_bar_missed():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),
        ("2025-06-23", 105.0, 106.0, 104.0, 105.0),          # 갭업 — low 104 > 100
    ])}
    rep = compute_next_bar_fill_whatif(plans, price)
    assert _row(rep, "X").next_bar_status == "missed"


def test_missing_next_bar_is_unknown():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0)])}   # 다음 바 없음(마지막)
    rep = compute_next_bar_fill_whatif(plans, price)
    assert _row(rep, "X").next_bar_status == "unknown"
    # 심볼 결측도 unknown.
    rep2 = compute_next_bar_fill_whatif(plans, {})
    assert _row(rep2, "X").next_bar_status == "unknown"


def test_same_bar_and_next_bar_can_differ():
    # 같은-바: open<=limit → filled. 다음-바: 갭업 → missed.
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),           # same-bar filled (open 100<=100)
        ("2025-06-23", 105.0, 106.0, 104.0, 105.0),          # next-bar missed
    ])}
    rep = compute_next_bar_fill_whatif(plans, price)
    r = _row(rep, "X")
    assert r.same_bar_status == "filled"
    assert r.next_bar_status == "missed"
    assert rep.same_bar_fill_rate == 1.0
    assert rep.next_bar_fill_rate == 0.0
    assert any("진입" in w or "lookahead" in w.lower() or "오해" in w for w in rep.warnings)


# --- 집계 / PnL ---


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


def test_missed_profitable_and_gap_and_best_worst():
    plans = _report([_plan("A", "2025-06-20", 100.0), _plan("B", "2025-06-20", 100.0)])
    price = {
        "A": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                    ("2025-06-23", 98.0, 99.0, 97.0, 98.0)]),       # next filled
        "B": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                    ("2025-06-23", 110.0, 111.0, 109.0, 110.0)]),   # next missed (수익 트레이드)
    }
    td = _td([("A", "2025-06-20", 5.0), ("B", "2025-06-20", 150.0)])
    rep = compute_next_bar_fill_whatif(plans, price, trade_diag=td)
    assert rep.next_filled_count == 1
    assert rep.next_missed_count == 1
    assert rep.missed_profitable_count == 1
    assert rep.worst_missed.symbol == "B" and rep.best_missed.symbol == "B"
    assert rep.avg_next_bar_gap is not None       # B는 +10% 갭업


def test_real_orders_zero_and_report_type():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 99.0, 100.0, 98.0, 99.5)])}
    rep = compute_next_bar_fill_whatif(plans, price)
    assert isinstance(rep, NextBarFillReport)
    assert rep.real_orders_placed == 0


# --- 불변 ---


def test_inputs_not_mutated():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 98.0, 99.0, 97.0, 98.0)])}
    before = price["X"].copy()
    plans_before = plans.plans
    compute_next_bar_fill_whatif(plans, price)
    pd.testing.assert_frame_equal(price["X"], before)
    assert plans.plans == plans_before


def test_format_contains_sections():
    plans = _report([_plan("X", "2025-06-20", 100.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 98.0, 99.0, 97.0, 98.0)])}
    rep = compute_next_bar_fill_whatif(plans, price)
    text = format_next_bar_fill_whatif(rep)
    assert "Next-Bar" in text
    assert "same" in text.lower() and "next" in text.lower()
    assert "real_orders_placed : 0" in text
