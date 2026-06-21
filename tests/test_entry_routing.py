"""entry_routing 테스트 (spec: specs/entry_routing.md).

심볼 갭 행태로 진입 실행 라우팅(3% limit vs next-open) 진단(리포트 전용). 갭 통계/분류/라우팅 선택/
aggressive 진단라벨/입력 불변. real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd

from agents.entry_routing import (
    RoutingReport,
    compute_entry_routing,
    compute_symbol_gap_stats,
    format_entry_routing,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg

_DATES = pd.date_range("2024-01-01", periods=120, freq="B")


def _leg(symbol, entry_i, pnl, *, exit_i=None):
    exit_d = str(_DATES[exit_i].date()) if exit_i is not None else str(_DATES[entry_i + 5].date())
    return TradeLeg(symbol=symbol, entry_date=str(_DATES[entry_i].date()), exit_date=exit_d,
                    entry_price=100.0, exit_price=100.0 + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / 100.0,
                    exit_reason="time_stop")


def _diag(legs):
    return TradeDiagnostics(trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
                            equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
                            top_veto_reasons=())


def _df_with_gaps(gaps_by_index):
    """index→갭(open/prev_close-1) 지정. 기본 종가 100 평탄, 지정 바만 open 갭."""
    n = 120
    close = np.full(n, 100.0)
    op = close.copy()
    for i, g in gaps_by_index.items():
        op[i] = close[i - 1] * (1 + g)
    return pd.DataFrame({"open": op, "high": np.maximum(op, close) * 1.01,
                         "low": np.minimum(op, close) * 0.99, "close": close}, index=_DATES)


def _pol(report, name):
    return next(p for p in report.policies if p.name == name)


def _sym(report, symbol):
    return next(s for s in report.symbols if s.symbol == symbol)


# --- 갭 통계 ---


def test_gap_stats_compute():
    df = _df_with_gaps({10: 0.025, 11: 0.01, 12: -0.01, 13: 0.05})
    stats = compute_symbol_gap_stats(df, [str(_DATES[i].date()) for i in (10, 11, 12, 13)])
    assert stats.n == 4
    assert stats.gap_up_freq == 0.75                  # 3/4 gap up
    assert stats.large_gap_up_freq_2pct == 0.5        # 0.025, 0.05 > 2%
    assert stats.large_gap_up_freq_3pct == 0.25       # only 0.05 > 3%
    assert stats.avg_gap is not None and stats.median_gap is not None


def test_gap_stats_empty_safe():
    stats = compute_symbol_gap_stats(None, ["2024-01-10"])
    assert stats.n == 0 and stats.gap_up_freq is None


# --- 분류 + 라우팅 ---


def _setup():
    # HIGH: 진입마다 큰 갭업 → next-open이 더 많은 진입/PnL. LOW: 평탄.
    high_df = _df_with_gaps({i: 0.03 for i in range(20, 40)})
    low_df = _df_with_gaps({i: 0.0 for i in range(20, 40)})
    price = {"HIGH": high_df, "LOW": low_df}
    # next-open이 HIGH에서 우수(갭업 진입 포착), LOW는 동일.
    limit3 = _diag([_leg("HIGH", 20, 10.0), _leg("LOW", 22, 30.0)])
    next_open = _diag([_leg("HIGH", 20, 50.0), _leg("HIGH", 25, 40.0), _leg("LOW", 22, 30.0)])
    entries = {"HIGH": [str(_DATES[i].date()) for i in range(20, 40)],
               "LOW": [str(_DATES[i].date()) for i in range(20, 40)]}
    return limit3, next_open, price, entries


def test_high_low_gap_classification():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price, high_gap_threshold=0.25)
    assert _sym(rep, "HIGH").is_high_gap is True       # large2 freq 1.0 >= 0.25
    assert _sym(rep, "LOW").is_high_gap is False


def test_conservative_routes_high_to_next_open():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price, high_gap_threshold=0.25)
    cons = _pol(rep, "gap_routed_conservative")
    assert cons.chosen_routes["HIGH"] == "next_open"   # 고갭 → next-open
    assert cons.chosen_routes["LOW"] == "limit"        # 저갭 → 3% limit


def test_aggressive_picks_winner_and_labeled_diagnostic():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price)
    agg = _pol(rep, "gap_routed_aggressive")
    assert agg.chosen_routes["HIGH"] == "next_open"    # next_open_pnl(90) > limit3(10)
    assert agg.chosen_routes["LOW"] == "limit"         # 동률(30=30) → limit 유지
    assert agg.is_diagnostic_only is True
    assert any("aggressive" in w.lower() or "overfit" in w.lower() or "진단" in w for w in rep.warnings)


def test_all_limit_all_next_open_totals():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price)
    assert _pol(rep, "all_limit_3pct").total_pnl == 40.0     # 10 + 30
    assert _pol(rep, "all_next_open").total_pnl == 120.0     # 90 + 30
    assert _pol(rep, "all_limit_3pct").cumulative_return == 40.0 / 1000.0


def test_symbol_diff_and_prefers():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price)
    h = _sym(rep, "HIGH")
    assert h.limit3_pnl == 10.0 and h.next_open_pnl == 90.0
    assert h.diff == 80.0
    assert h.prefers == "next_open"


# --- fail-safe / 불변 ---


def test_real_orders_zero_and_type():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price)
    assert isinstance(rep, RoutingReport)
    assert rep.real_orders_placed == 0
    assert all(p.real_orders_placed == 0 for p in rep.policies)


def test_inputs_not_mutated():
    limit3, next_open, price, _ = _setup()
    before_l, before_n = limit3.trades, next_open.trades
    df_before = price["HIGH"].copy()
    compute_entry_routing(limit3, next_open, price)
    assert limit3.trades == before_l and next_open.trades == before_n
    pd.testing.assert_frame_equal(price["HIGH"], df_before)


def test_format_contains_sections():
    limit3, next_open, price, _ = _setup()
    rep = compute_entry_routing(limit3, next_open, price)
    text = format_entry_routing(rep)
    assert "Entry Execution Routing" in text
    assert "gap_routed_conservative" in text
    assert "diagnostic" in text.lower() or "진단" in text
    assert "real_orders_placed : 0" in text


# --- 러너: 베이스라인 잠금(60일) + 두 실행만 사용 ---


def test_runner_uses_locked_baseline_and_two_executions(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import entry_routing_diagnostics as erd

    monkeypatch.setattr(erd.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(erd.run_sim, "_feature_inputs", lambda a: ({}, None))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.max_holding_days, args.stop_loss_pct,
                         args.trailing_stop_pct, args.share_mode, tuple(args.weekend_exit_symbols)))
        return _result_stub()

    report, error = erd.run_routing_diagnostics(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    models = [c[0] for c in captured]
    assert models == ["next-bar-limit", "next-open"]      # 두 실행만
    for _, mh, stop, trail, sm, wk in captured:
        assert mh == 60                                   # 베이스라인 60일 잠금
        assert stop == 0.15 and trail == 0.20 and sm == "fractional"
        assert wk == ()                                   # 일반주 — weekend 비움
    assert report.real_orders_placed == 0


def _result_stub():
    from types import SimpleNamespace
    multiday = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
    return SimpleNamespace(multiday=multiday, performance=SimpleNamespace(
        cumulative_return=0.26, max_drawdown=0.07, win_rate=0.55, total_pnl=260.0,
        num_trades=48, num_closed_trades=48), real_orders_placed=0, portfolio=multiday.portfolio)
