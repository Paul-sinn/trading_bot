"""execution_stress 테스트 (spec: specs/execution_stress.md).

3% limit vs next-open을 슬리피지 + 갭가드 하에서 비교(리포트 전용, 사후 적용 — 원 결과 불변).
real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd

from agents.execution_stress import (
    StressReport,
    compute_execution_stress,
    format_execution_stress,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg

_DATES = pd.date_range("2024-01-01", periods=120, freq="B")


def _leg(symbol, entry_i, pnl, *, entry_px=100.0):
    return TradeLeg(symbol=symbol, entry_date=str(_DATES[entry_i].date()),
                    exit_date=str(_DATES[entry_i + 5].date()), entry_price=entry_px,
                    exit_price=entry_px + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / entry_px,
                    exit_reason="time_stop")


def _diag(legs):
    return TradeDiagnostics(trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
                            equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
                            top_veto_reasons=())


def _df_with_gaps(gaps_by_index):
    n = 120
    close = np.full(n, 100.0)
    op = close.copy()
    for i, g in gaps_by_index.items():
        op[i] = close[i - 1] * (1 + g)
    return pd.DataFrame({"open": op, "high": np.maximum(op, close) * 1.01,
                         "low": np.minimum(op, close) * 0.99, "close": close}, index=_DATES)


def _res(report, policy, slip, guard):
    return next(r for r in report.results
               if r.policy == policy and abs(r.slippage_pct - slip) < 1e-9 and r.gap_guard == guard)


# --- 그리드 ---


def test_stress_grid_runs_both_policies():
    limit3 = _diag([_leg("A", 20, 50.0)])
    next_open = _diag([_leg("A", 20, 60.0)])
    price = {"A": _df_with_gaps({20: 0.01})}
    rep = compute_execution_stress(limit3, next_open, price)
    assert isinstance(rep, StressReport)
    pols = {r.policy for r in rep.results}
    assert pols == {"next-bar-limit-3%", "next-open"}
    # 5 슬리피지 × 2 정책 + next-open 가드 3개.
    assert len(rep.results) == 5 + 5 + 3
    assert rep.real_orders_placed == 0


# --- 슬리피지 ---


def test_slippage_reduces_pnl_without_mutating_inputs():
    legs = [_leg("A", 20, 50.0, entry_px=100.0)]
    limit3 = _diag(legs)
    next_open = _diag([_leg("A", 20, 50.0)])
    price = {"A": _df_with_gaps({20: 0.0})}
    before = limit3.trades
    rep = compute_execution_stress(limit3, next_open, price)
    no_slip = _res(rep, "next-bar-limit-3%", 0.0, None)
    slip_1pct = _res(rep, "next-bar-limit-3%", 0.01, None)
    # 1% 슬리피지 → entry 100×0.01×1 = 1.0 만큼 PnL 감소.
    assert no_slip.total_pnl == 50.0
    assert slip_1pct.total_pnl == 49.0
    assert limit3.trades == before                  # 원 diag 불변


# --- 갭 가드 ---


def test_gap_guard_skips_entries_above_threshold():
    # A: 진입 갭 6% (>5%, >3%, <8%). B: 갭 1%.
    limit3 = _diag([_leg("A", 20, 40.0), _leg("B", 30, 20.0)])
    next_open = _diag([_leg("A", 20, 40.0), _leg("B", 30, 20.0)])
    price = {"A": _df_with_gaps({20: 0.06}), "B": _df_with_gaps({30: 0.01})}
    rep = compute_execution_stress(limit3, next_open, price)
    guard3 = _res(rep, "next-open", 0.0025, 0.03)    # 3% 가드 → A(6%) skip
    guard8 = _res(rep, "next-open", 0.0025, 0.08)    # 8% 가드 → A(6%) 통과
    assert guard3.skipped_gap_entries == 1
    assert guard3.skipped_profitable_pnl > 0         # A는 수익(40)이었음
    assert guard8.skipped_gap_entries == 0
    assert guard3.trades == 1                        # B만 남음
    assert guard8.trades == 2


def test_limit_policy_has_no_gap_guard_rows():
    limit3 = _diag([_leg("A", 20, 40.0)])
    next_open = _diag([_leg("A", 20, 40.0)])
    price = {"A": _df_with_gaps({20: 0.01})}
    rep = compute_execution_stress(limit3, next_open, price)
    assert all(r.gap_guard is None for r in rep.results if r.policy == "next-bar-limit-3%")


# --- 메트릭 / 불변 ---


def test_metrics_and_top_share():
    limit3 = _diag([_leg("AMD", 20, 100.0), _leg("NVDA", 25, 20.0)])
    next_open = _diag([_leg("AMD", 20, 120.0), _leg("NVDA", 25, 20.0)])
    price = {"AMD": _df_with_gaps({20: 0.01}), "NVDA": _df_with_gaps({25: 0.01})}
    rep = compute_execution_stress(limit3, next_open, price, starting_cash=1000.0)
    r = _res(rep, "next-open", 0.0, None)
    assert r.total_pnl == 140.0
    assert r.cumulative_return == 0.14
    assert r.top_symbol == "AMD"
    assert r.trades == 2
    assert r.real_orders_placed == 0


def test_inputs_not_mutated():
    limit3 = _diag([_leg("A", 20, 40.0)])
    next_open = _diag([_leg("A", 20, 50.0)])
    price = {"A": _df_with_gaps({20: 0.06})}
    bl, bn = limit3.trades, next_open.trades
    df_before = price["A"].copy()
    compute_execution_stress(limit3, next_open, price)
    assert limit3.trades == bl and next_open.trades == bn
    pd.testing.assert_frame_equal(price["A"], df_before)


def test_format_contains_sections():
    limit3 = _diag([_leg("A", 20, 40.0)])
    next_open = _diag([_leg("A", 20, 50.0)])
    price = {"A": _df_with_gaps({20: 0.01})}
    rep = compute_execution_stress(limit3, next_open, price)
    text = format_execution_stress(rep)
    assert "Execution Stress" in text
    assert "slip" in text.lower()
    assert "skip" in text.lower()        # skip / skipPnL 컬럼
    assert "real_orders_placed : 0" in text


# --- 러너: 베이스라인 잠금 ---


def test_runner_locks_baseline(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import entry_execution_stress as ees

    monkeypatch.setattr(ees.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(ees.run_sim, "_feature_inputs", lambda a: ({}, None))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.max_holding_days, args.stop_loss_pct,
                         args.trailing_stop_pct, args.share_mode, tuple(args.weekend_exit_symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=SimpleNamespace(
            cumulative_return=0.26, max_drawdown=0.07, win_rate=0.55, total_pnl=260.0,
            num_trades=48, num_closed_trades=48), real_orders_placed=0, portfolio=md.portfolio)

    report, error = ees.run_execution_stress(data_root="x", events_csv=None, assume_no_events=True,
                                             simulate_fn=_fake)
    assert error is None
    assert [c[0] for c in captured] == ["next-bar-limit", "next-open"]
    for _, mh, stop, trail, sm, wk in captured:
        assert mh == 60 and stop == 0.15 and trail == 0.20 and sm == "fractional" and wk == ()
    assert report.real_orders_placed == 0
