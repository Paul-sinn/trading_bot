"""baseline_robustness 테스트 (spec: specs/baseline_robustness.md).

잠긴 next-bar-limit 3% 베이스라인의 전략 강건성(윈도우/LOO/벤치마크/슬리피지/집중/청산사유). 리포트 전용,
입력 불변. next-open/winner extension 미사용. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.baseline_robustness import (
    BaselineRobustness,
    build_baseline_robustness,
    compute_concentration,
    compute_exit_reason_distribution,
    compute_full_result,
    compute_slippage_stress,
    format_baseline_robustness,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg


def _leg(symbol, pnl, *, entry_px=100.0, reason="time_stop"):
    return TradeLeg(symbol=symbol, entry_date="2025-01-02", exit_date="2025-02-02", entry_price=entry_px,
                    exit_price=entry_px + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / entry_px, exit_reason=reason)


def _diag(legs):
    return TradeDiagnostics(trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
                            equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
                            top_veto_reasons=())


def _perf(ret=0.75, mdd=0.13, win=0.65, pnl=750.0, trades=80):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


def _win(label, ret, pnl, mdd=0.05, trades=5):
    return SimpleNamespace(label=label, return_pct=ret, pnl=pnl, max_drawdown=mdd, trade_count=trades)


def _loo(sym, total, diff, ret=None):
    return SimpleNamespace(excluded_symbol=sym, total_pnl=total, total_pnl_diff=diff,
                           return_pct=ret, max_drawdown=None, mode="rerun")


def _robustness(windows, loo=(), symbol_perf=()):
    rated = [w for w in windows if w.return_pct is not None]
    best = max(rated, key=lambda w: w.return_pct) if rated else None
    worst = min(rated, key=lambda w: w.return_pct) if rated else None
    return SimpleNamespace(windows=tuple(windows), best_window=best, worst_window=worst,
                           leave_one_out=tuple(loo), symbol_perf=tuple(symbol_perf))


def _bench(spy=0.5, qqq=0.6, missing_qqq=False):
    baselines = [SimpleNamespace(name="SPY buy-hold", symbol="SPY", cumulative_return=spy,
                                 return_diff_vs_strategy=0.25, note=None)]
    if missing_qqq:
        baselines.append(SimpleNamespace(name="QQQ buy-hold", symbol="QQQ", cumulative_return=None,
                                         return_diff_vs_strategy=None, note="QQQ 데이터 없음"))
    else:
        baselines.append(SimpleNamespace(name="QQQ buy-hold", symbol="QQQ", cumulative_return=qqq,
                                         return_diff_vs_strategy=0.15, note=None))
    return SimpleNamespace(baselines=tuple(baselines))


# --- 풀기간 ---


def test_full_result_ratio():
    f = compute_full_result(_perf(ret=0.75, mdd=0.15, pnl=750.0, trades=80))
    assert f.cumulative_return == 0.75
    assert f.return_over_mdd == 0.75 / 0.15
    assert f.trades == 80


def test_full_result_zero_mdd_safe():
    f = compute_full_result(_perf(mdd=0.0))
    assert f.return_over_mdd is None


# --- 슬리피지 (원본 불변) ---


def test_slippage_stress_does_not_mutate_original():
    legs = [_leg("A", 50.0), _leg("B", 70.0)]
    diag = _diag(legs)
    before = [l.pnl for l in diag.trades]
    grid = compute_slippage_stress(diag, slippages=(0.0, 0.01), starting_cash=1000.0)
    assert [l.pnl for l in diag.trades] == before          # 원본 leg 미변형
    g0 = next(s for s in grid if s.slippage == 0.0)
    assert g0.total_pnl == 120.0 and g0.return_pct == 0.12
    g1 = next(s for s in grid if s.slippage == 0.01)
    # 1% 슬리피지: 각 leg entry 100×0.01=1 차감 → 49 + 69 = 118.
    assert g1.total_pnl == 118.0


# --- 청산 사유 ---


def test_exit_reason_distribution_buckets():
    legs = [_leg("A", 10, reason="time_stop"), _leg("B", -5, reason="stop_loss_hit"),
            _leg("C", 20, reason="trailing_stop_hit"), _leg("D", 3, reason="manual_sim_exit"),
            _leg("E", 0, reason="OPEN")]
    out = compute_exit_reason_distribution(_diag(legs))
    by = {e.reason: e for e in out}
    assert by["time_stop"].count == 1
    assert by["stop_loss"].count == 1
    assert by["trailing_stop"].count == 1
    assert by["other"].count == 1                          # manual_sim_exit → other
    assert "OPEN" not in by                                 # 미청산 제외
    assert abs(sum(e.share_of_trades for e in out) - 1.0) < 1e-9


# --- 집중 ---


def test_concentration_top_and_worst():
    totals = {"MU": 250.0, "AMD": 150.0, "NVDA": 100.0, "AAPL": -20.0}
    c = compute_concentration(totals)
    assert c.top_symbol == "MU"
    assert abs(c.top1_share - 250.0 / 500.0) < 1e-9        # 양수합 500
    assert c.top3_symbols == ("MU", "AMD", "NVDA")
    assert abs(c.top3_share - 500.0 / 500.0) < 1e-9
    assert c.worst_symbol == "MU"                          # 빼면 가장 손해
    assert c.worst_removal_delta == -250.0


# --- build / 윈도우·LOO·벤치마크 결측 ---


def test_window_split_and_loo_flow():
    windows = [_win("2025-Q1", 0.1, 100.0), _win("2025-Q2", 0.2, 200.0)]
    loo = [_loo("MU", 500.0, -250.0, ret=0.5), _loo("AMD", 720.0, -30.0, ret=0.72)]
    rob = _robustness(windows, loo=loo)
    rep = build_baseline_robustness(compute_full_result(_perf()), rob, _bench(),
                                    compute_slippage_stress(_diag([_leg("A", 50)]), slippages=(0.0,), starting_cash=1000.0),
                                    (), compute_concentration({"MU": 250.0, "AMD": 250.0}))
    assert rep.best_window.label == "2025-Q2"
    assert rep.worst_window.label == "2025-Q1"
    assert rep.window_positive_share == 1.0
    assert len(rep.robustness.leave_one_out) == 2


def test_benchmark_missing_handled_safely():
    rob = _robustness([_win("2025-Q1", 0.1, 100.0)])
    rep = build_baseline_robustness(compute_full_result(_perf(ret=0.75)), rob, _bench(missing_qqq=True),
                                    compute_slippage_stress(_diag([_leg("A", 50)]), slippages=(0.0,), starting_cash=1000.0),
                                    (), compute_concentration({"A": 100.0}))
    assert rep.beats_spy is True                            # 0.75 > 0.5
    assert rep.beats_qqq is None                            # QQQ 결측 → 안전 처리


def test_is_robust_true_when_broad_and_survives():
    windows = [_win("Q1", 0.1, 100.0), _win("Q2", 0.2, 200.0)]
    rob = _robustness(windows)
    slip = compute_slippage_stress(_diag([_leg("A", 250), _leg("B", 250), _leg("C", 250)]),
                                   slippages=(0.0, 0.01), starting_cash=1000.0)
    rep = build_baseline_robustness(compute_full_result(_perf(ret=0.75)), rob, _bench(spy=0.4),
                                    slip, (), compute_concentration({"A": 250.0, "B": 250.0, "C": 250.0}))
    assert rep.is_robust is True
    assert rep.real_orders_placed == 0


def test_is_robust_false_when_concentrated_and_collapse():
    windows = [_win("Q1", 0.1, 100.0), _win("Q2", 0.2, 200.0)]
    rob = _robustness(windows)
    slip = compute_slippage_stress(_diag([_leg("A", 700)]), slippages=(0.0,), starting_cash=1000.0)
    # A가 양수 PnL의 78% + 제거 시 붕괴.
    rep = build_baseline_robustness(compute_full_result(_perf(ret=0.75, pnl=900.0)), rob, _bench(spy=0.4),
                                    slip, (), compute_concentration({"A": 700.0, "B": 100.0, "C": 100.0}))
    assert rep.is_robust is False
    assert any("집중" in w or "붕괴" in w for w in rep.warnings)


def test_format_contains_sections():
    rob = _robustness([_win("2025-Q1", 0.1, 100.0)], loo=[_loo("MU", 500.0, -250.0)])
    rep = build_baseline_robustness(compute_full_result(_perf()), rob, _bench(),
                                    compute_slippage_stress(_diag([_leg("A", 50)]), slippages=(0.0, 0.01), starting_cash=1000.0),
                                    compute_exit_reason_distribution(_diag([_leg("A", 50, reason="time_stop")])),
                                    compute_concentration({"MU": 250.0}))
    text = format_baseline_robustness(rep)
    assert "Realistic Baseline Robustness" in text
    assert "leave-one-symbol-out" in text
    assert "exit reason distribution" in text
    assert "slippage stress" in text
    assert "real_orders_placed : 0" in text


# --- 러너: 잠긴 베이스라인 / next-open·winner extension 미사용 ---


def test_runner_locked_baseline_no_next_open(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import baseline_robustness as brun

    monkeypatch.setattr(brun.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(brun.run_sim, "_feature_inputs", lambda a: ({}, None))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.max_holding_days,
                         args.stop_loss_pct, args.trailing_stop_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(), real_orders_placed=0, portfolio=md.portfolio)

    report, error = brun.run_baseline_robustness(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    model, buf, mh, stop, trail, sm, wk = captured[0]
    assert model == "next-bar-limit" and model != "next-open"
    assert buf == 0.03 and mh == 60 and stop == 0.15 and trail == 0.20 and sm == "fractional" and wk == ()
    assert not any("winner" in str(c).lower() or "gap" in str(c).lower() for c in captured)
    assert report.real_orders_placed == 0
    assert isinstance(report, BaselineRobustness)
