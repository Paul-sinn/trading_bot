"""entry_limit_sensitivity 테스트 (spec: specs/entry_limit_sensitivity.md).

여러 limit 버퍼의 다음-바 체결을 비교(what-if 전용). 넓은 버퍼는 체결률 비감소, marketable은 다음 바
있으면 체결, 다음 바 결측은 unknown. 실 체결 불변. real_orders=0. 네트워크 없음.
"""

import pandas as pd
import pytest

from agents.entry_limit_sensitivity import (
    EntryLimitSensitivityReport,
    compute_entry_limit_sensitivity,
    format_entry_limit_sensitivity,
    generate_buffers,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _bars(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d, *_ in rows])
    return pd.DataFrame(
        {"open": [o for _, o, h, l, c in rows], "high": [h for _, o, h, l, c in rows],
         "low": [l for _, o, h, l, c in rows], "close": [c for _, o, h, l, c in rows]},
        index=idx,
    )


def _td(entries):
    """entries: [(symbol, entry_date, ref, pnl)] → TradeDiagnostics."""
    legs = [
        TradeLeg(symbol=s, entry_date=d, exit_date="2025-12-31", entry_price=ref,
                 exit_price=ref + p, qty=1.0, pnl=p, pnl_pct=p / ref, exit_reason="sell")
        for s, d, ref, p in entries
    ]
    return TradeDiagnostics(
        trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
        equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


def _policy(report, name_part):
    return next(p for p in report.policies if name_part in p.name)


# --- 그리드 ---


def test_buffer_grid_generation():
    buffers = generate_buffers()
    assert buffers == (0.005, 0.01, 0.015, 0.02, 0.03)


# --- 체결 모델 ---


def test_wider_buffer_increases_or_preserves_fill_rate():
    # 다음 바가 +1.5% 갭업 → 0.5%/1.0% 미체결, 1.5%+ 체결.
    td = _td([("X", "2025-06-20", 100.0, 10.0)])
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),
        ("2025-06-23", 101.5, 103.0, 101.2, 102.0),    # next_open 101.5, next_low 101.2
    ])}
    rep = compute_entry_limit_sensitivity(td, price)
    rates = [p.fill_rate for p in rep.policies if p.buffer_pct is not None]
    # 버퍼 오름차순으로 체결률 비감소.
    assert all(a <= b for a, b in zip(rates, rates[1:]) if a is not None and b is not None)
    assert _policy(rep, "0.5%").fill_rate == 0.0      # 빡빡 → 미체결
    assert _policy(rep, "2.0%").fill_rate == 1.0      # 넓음 → 체결


def test_marketable_proxy_fills_when_next_bar_exists():
    td = _td([("X", "2025-06-20", 100.0, 5.0)])
    price = {"X": _bars([
        ("2025-06-20", 100.0, 101.0, 99.0, 100.0),
        ("2025-06-23", 108.0, 109.0, 107.0, 108.0),    # 큰 갭업 — 모든 버퍼 미체결, marketable만 체결
    ])}
    rep = compute_entry_limit_sensitivity(td, price)
    mk = _policy(rep, "marketable")
    assert mk.fill_rate == 1.0
    assert mk.filled == 1
    assert mk.is_marketable is True
    # 버퍼 3%도 +8% 갭은 못 잡음.
    assert _policy(rep, "3.0%").fill_rate == 0.0


def test_missing_next_bar_stays_unknown():
    td = _td([("X", "2025-06-20", 100.0, 5.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0)])}   # 다음 바 없음
    rep = compute_entry_limit_sensitivity(td, price)
    for p in rep.policies:
        assert p.unknown == 1
        assert p.filled == 0 and p.missed == 0
        assert p.fill_rate is None      # 알려진 게 없음


def test_est_pnl_proxy_sums_filled():
    td = _td([("A", "2025-06-20", 100.0, 30.0), ("B", "2025-06-20", 100.0, -10.0)])
    price = {
        "A": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                    ("2025-06-23", 99.0, 100.0, 98.0, 99.0)]),     # 체결(open<=limit)
        "B": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                    ("2025-06-23", 99.5, 100.0, 98.0, 99.0)]),     # 체결
    }
    rep = compute_entry_limit_sensitivity(td, price)
    p = _policy(rep, "0.5%")
    assert p.filled == 2
    assert p.est_filled_pnl == pytest.approx(20.0)    # 30 + (-10)


# --- fail-safe / 불변 ---


def test_real_orders_zero_and_type():
    td = _td([("X", "2025-06-20", 100.0, 5.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 99.0, 100.0, 98.0, 99.0)])}
    rep = compute_entry_limit_sensitivity(td, price)
    assert isinstance(rep, EntryLimitSensitivityReport)
    assert rep.real_orders_placed == 0
    assert all(p.real_orders_placed == 0 for p in rep.policies)


def test_inputs_not_mutated():
    td = _td([("X", "2025-06-20", 100.0, 5.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 99.0, 100.0, 98.0, 99.0)])}
    before = price["X"].copy()
    trades_before = td.trades
    compute_entry_limit_sensitivity(td, price)
    pd.testing.assert_frame_equal(price["X"], before)
    assert td.trades == trades_before


def test_recommended_and_format():
    td = _td([("X", "2025-06-20", 100.0, 5.0)])
    price = {"X": _bars([("2025-06-20", 100.0, 101.0, 99.0, 100.0),
                         ("2025-06-23", 99.0, 100.0, 98.0, 99.0)])}   # 모두 체결
    rep = compute_entry_limit_sensitivity(td, price)
    assert rep.recommended is not None
    text = format_entry_limit_sensitivity(rep)
    assert "Entry Limit Sensitivity" in text
    assert "marketable" in text.lower()
    assert "fill_rate" in text.lower()
    assert "real_orders_placed : 0" in text
