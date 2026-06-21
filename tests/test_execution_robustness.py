"""execution_robustness 테스트 (spec: specs/execution_robustness.md).

next-open vs 3% limit 강건성(시간창/LOO/슬리피지/집중). 리포트 전용, 입력 불변. real_orders=0. 네트워크 없음.
"""

from types import SimpleNamespace

import pandas as pd

from agents.execution_robustness import (
    PolicySummary,
    RobustnessValidation,
    build_validation,
    compute_leave_one_out,
    compute_slippage_robustness,
    compute_window_comparison,
    format_robustness_validation,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _win(label, ret, pnl, mdd=0.05, trades=5):
    return SimpleNamespace(label=label, return_pct=ret, pnl=pnl, max_drawdown=mdd, trade_count=trades)


def _leg(symbol, pnl, entry_px=100.0):
    return TradeLeg(symbol=symbol, entry_date="2025-01-02", exit_date="2025-02-02", entry_price=entry_px,
                    exit_price=entry_px + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / entry_px, exit_reason="time_stop")


def _diag(legs):
    return TradeDiagnostics(trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
                            equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
                            top_veto_reasons=())


def _summary(ret, mdd, win, pnl, trades):
    return PolicySummary(cumulative_return=ret, max_drawdown=mdd, win_rate=win, total_pnl=pnl, trades=trades)


# --- 윈도우 ---


def test_window_comparison():
    lim = [_win("2025-Q1", 0.05, 50.0), _win("2025-Q2", 0.10, 100.0)]
    nxt = [_win("2025-Q1", 0.08, 80.0), _win("2025-Q2", 0.06, 60.0)]
    cmp = compute_window_comparison(lim, nxt)
    by = {w.label: w for w in cmp}
    assert by["2025-Q1"].next_open_wins is True       # 0.08 > 0.05
    assert by["2025-Q2"].next_open_wins is False       # 0.06 < 0.10


# --- LOO ---


def test_leave_one_out():
    loo = compute_leave_one_out(1000.0, {"AMD": 700.0, "NVDA": 980.0, "MU": 1010.0})
    by = {l.dropped_symbol: l for l in loo}
    assert by["AMD"].delta_vs_full == -300.0           # AMD 빼면 −300
    assert by["AMD"].pct_of_full == 0.30
    assert by["MU"].delta_vs_full == 10.0              # MU 빼면 오히려 +10


# --- 슬리피지 ---


def test_slippage_robustness():
    limit3 = _diag([_leg("A", 50.0)])
    next_open = _diag([_leg("A", 70.0)])
    grid = compute_slippage_robustness(limit3, next_open, slippages=(0.0, 0.01), starting_cash=1000.0)
    g0 = next(s for s in grid if s.slippage == 0.0)
    assert g0.limit3_return == 0.05 and g0.next_open_return == 0.07
    assert g0.next_open_wins is True
    g1 = next(s for s in grid if s.slippage == 0.01)
    # 1% 슬리피지: 둘 다 entry 100×0.01=1 차감 → 49 vs 69, next-open 여전히 우위.
    assert g1.next_open_return == 0.069
    assert g1.next_open_wins is True


# --- 집중 / is_robust ---


def _robust_inputs(*, symbol_pnl, windows_next_higher=True, slip_wins=True):
    lim = _summary(0.75, 0.13, 0.65, 750.0, 80)
    nxt = _summary(0.90, 0.13, 0.79, 900.0, 77)
    if windows_next_higher:
        windows = compute_window_comparison(
            [_win("Q1", 0.3, 300.0), _win("Q2", 0.4, 400.0)],
            [_win("Q1", 0.4, 400.0), _win("Q2", 0.5, 500.0)])
    else:
        windows = compute_window_comparison(
            [_win("Q1", 0.3, 300.0), _win("Q2", 0.4, 400.0)],
            [_win("Q1", 0.2, 200.0), _win("Q2", 0.5, 500.0)])
    nv = 900.0 if slip_wins else 700.0
    slippage = (SimpleNamespace(slippage=0.0, limit3_return=0.75, next_open_return=nv / 1000, next_open_wins=nv > 750),)
    loo = compute_leave_one_out(900.0, {s: 900.0 - 50 for s in symbol_pnl})
    return lim, nxt, windows, loo, slippage, symbol_pnl


def test_concentration_warning_one_symbol():
    # AMD가 양수 PnL의 60% → 경고.
    symbol_pnl = {"AMD": 600.0, "NVDA": 200.0, "MU": 200.0}
    inp = _robust_inputs(symbol_pnl=symbol_pnl)
    rep = build_validation(*inp)
    assert any("35%" in w or "집중" in w for w in rep.warnings)


def test_is_robust_true_when_broad_and_consistent():
    symbol_pnl = {"AMD": 250.0, "NVDA": 250.0, "MU": 250.0, "ARM": 150.0}   # 최대 share < 35%
    inp = _robust_inputs(symbol_pnl=symbol_pnl)
    rep = build_validation(*inp)
    assert isinstance(rep, RobustnessValidation)
    assert rep.is_robust is True
    assert rep.real_orders_placed == 0


def test_is_robust_false_when_concentrated():
    symbol_pnl = {"AMD": 700.0, "NVDA": 100.0, "MU": 100.0}                 # AMD 78% > 35%
    inp = _robust_inputs(symbol_pnl=symbol_pnl)
    rep = build_validation(*inp)
    assert rep.is_robust is False


def test_format_contains_sections():
    symbol_pnl = {"AMD": 250.0, "NVDA": 250.0, "MU": 250.0}
    rep = build_validation(*_robust_inputs(symbol_pnl=symbol_pnl))
    text = format_robustness_validation(rep)
    assert "Execution Robustness" in text
    assert "leave-one" in text.lower() or "LOO" in text
    assert "real_orders_placed : 0" in text


# --- 러너: 갭 가드 미적용 / 베이스라인 60 ---


def test_runner_no_gap_guard_locked_baseline(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import execution_robustness as erun

    monkeypatch.setattr(erun.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(erun.run_sim, "_feature_inputs", lambda a: ({}, None))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.max_holding_days, args.stop_loss_pct,
                         args.trailing_stop_pct, args.share_mode, tuple(args.weekend_exit_symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=SimpleNamespace(
            cumulative_return=0.9, max_drawdown=0.13, win_rate=0.79, total_pnl=900.0,
            num_trades=77, num_closed_trades=77), real_orders_placed=0, portfolio=md.portfolio)

    report, error = erun.run_execution_robustness(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    assert [c[0] for c in captured] == ["next-bar-limit", "next-open"]      # LOO는 빈 유니버스라 0
    for _, mh, stop, trail, sm, wk in captured:
        assert mh == 60 and stop == 0.15 and trail == 0.20 and sm == "fractional" and wk == ()
    assert report.real_orders_placed == 0
    assert not any("gap" in str(c).lower() for c in captured)               # 갭 가드 인자 없음
