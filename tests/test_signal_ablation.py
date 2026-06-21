"""signal_ablation 테스트 (spec: specs/signal_ablation.md).

시그널 제거 테스트(실험 전용). 청산/심볼 true-rerun + 모멘텀/볼륨/추세 shadow 근사. 베이스라인 파라미터·
기본 유니버스·프로덕션 로직 불변. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.signal_ablation import (
    MODE_SHADOW,
    MODE_TRUE,
    AblationReport,
    build_ablation,
    format_ablation_markdown,
    quarterly_pnl,
    shadow_drop,
    summarize,
)
from agents.trade_diagnostics import TradeLeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.signal_ablation_test as sat  # noqa: E402


def _leg(symbol, pnl, *, entry="2025-01-02", exit="2025-02-02", entry_px=100.0):
    return TradeLeg(symbol=symbol, entry_date=entry, exit_date=exit, entry_price=entry_px,
                    exit_price=entry_px + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / entry_px,
                    exit_reason="time_stop")


def _snap(mom=None, vol=None, up=None):
    return SimpleNamespace(momentum_score=mom, volume_ratio_20d=vol, price_above_20ma=up)


def _perf(ret=0.75, mdd=0.13, win=0.65, pnl=750.0, trades=80):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


# --- summarize ---


def test_summarize_shadow_no_performance():
    legs = [_leg("MU", 200.0), _leg("AMD", 100.0), _leg("NVDA", -40.0)]
    r = summarize("s", MODE_SHADOW, legs, starting_cash=1000.0, spy=0.1, qqq=0.2, note="x")
    assert r.mode == MODE_SHADOW
    assert r.max_drawdown is None and r.return_over_mdd is None     # shadow MDD n/a
    assert abs(r.cumulative_return - 260.0 / 1000.0) < 1e-9
    assert r.trades == 3
    assert abs(r.avg_trade_pnl - 260.0 / 3) < 1e-9
    assert r.median_trade_pnl == 100.0
    assert r.top1_symbol == "MU"
    assert r.beats_spy is True and r.beats_qqq is True


def test_summarize_true_uses_performance():
    legs = [_leg("MU", 200.0)]
    r = summarize("baseline", MODE_TRUE, legs, starting_cash=1000.0, performance=_perf(ret=0.75, mdd=0.15),
                  spy=0.4, qqq=0.55)
    assert r.cumulative_return == 0.75
    assert r.max_drawdown == 0.15
    assert r.return_over_mdd == 0.75 / 0.15
    assert r.beats_spy is True and r.beats_qqq is True


# --- shadow_drop ---


def test_shadow_drop_numeric_below_median_removed():
    legs = [_leg("A", 1), _leg("B", 2), _leg("C", 3), _leg("D", 4)]
    idx = {("A", "2025-01-02"): _snap(mom=0.1), ("B", "2025-01-02"): _snap(mom=0.5),
           ("C", "2025-01-02"): _snap(mom=0.9)}   # D: 스냅샷 없음 → 유지
    kept = shadow_drop(legs, idx, "momentum_score")
    names = {l.symbol for l in kept}
    assert "A" not in names                       # 중앙값(0.5) 미만 제거
    assert {"B", "C", "D"} <= names               # >=중앙값 + 판단불가 유지


def test_shadow_drop_flag_false_removed():
    legs = [_leg("A", 1), _leg("B", 2), _leg("C", 3)]
    idx = {("A", "2025-01-02"): _snap(up=True), ("B", "2025-01-02"): _snap(up=False),
           ("C", "2025-01-02"): _snap(up=None)}
    kept = {l.symbol for l in shadow_drop(legs, idx, "price_above_20ma", is_flag=True)}
    assert "B" not in kept                         # 하락추세 제거
    assert {"A", "C"} <= kept                       # 상승 + 판단불가 유지


def test_quarterly_pnl_groups_by_exit_quarter():
    legs = [_leg("A", 10, exit="2025-02-10"), _leg("B", 20, exit="2025-05-10"),
            _leg("C", 5, exit="2025-02-20")]
    q = dict(quarterly_pnl(legs))
    assert q["2025-Q1"] == 15.0 and q["2025-Q2"] == 20.0


# --- build / format ---


def test_build_flags_shadow_and_mu_and_no_exit():
    base = summarize("baseline", MODE_TRUE, [_leg("MU", 400.0), _leg("AMD", 350.0)],
                     starting_cash=1000.0, performance=_perf(pnl=750.0))
    no_exit = summarize("no_exit_controls", MODE_TRUE, [_leg("MU", 500.0), _leg("AMD", 400.0)],
                        starting_cash=1000.0, performance=_perf(pnl=900.0))
    no_mu = summarize("no_MU", MODE_TRUE, [_leg("AMD", 350.0)], starting_cash=1000.0,
                      performance=_perf(pnl=350.0))
    shadow = summarize("shadow_drop_low_momentum", MODE_SHADOW, [_leg("MU", 400.0)], starting_cash=1000.0)
    rep = build_ablation([base, no_exit, no_mu, shadow])
    assert any("청산 통제" in w for w in rep.warnings)        # no_exit > baseline
    assert any("MU 의존" in w for w in rep.warnings)          # 750→350
    assert any("shadow-approx" in w for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_format_marks_modes_and_shadow():
    base = summarize("baseline", MODE_TRUE, [_leg("MU", 400.0)], starting_cash=1000.0,
                     performance=_perf(), spy=0.4, qqq=0.55, eq=1.2)
    shadow = summarize("shadow_drop_low_momentum", MODE_SHADOW, [_leg("MU", 300.0)], starting_cash=1000.0)
    md = format_ablation_markdown(build_ablation([base, shadow]))
    assert "Signal Ablation Test" in md
    assert "shadow-approx" in md and "true-rerun" in md
    assert "mode 범례" in md
    assert "real_orders_placed = 0" in md


# --- 상수 잠금 ---


def test_locked_baseline_constants_and_universe_reused():
    assert sat._FILL_MODEL == "next-bar-limit"
    assert sat._BUFFER == 0.03 and sat._STOP == 0.15 and sat._TRAIL == 0.20
    assert sat._MAX_HOLD == 60 and sat._SHARE_MODE == "fractional"
    # 기본 유니버스는 universe_bias_test 단일 출처에서 재사용(중복/변경 없음).
    import experiments.universe_bias_test as ubt
    assert sat.BASELINE_UNIVERSE is ubt.BASELINE_UNIVERSE
    assert sat.LEVERAGED_ETFS.isdisjoint(sat.BASELINE_UNIVERSE)


def test_run_sim_defaults_unchanged():
    args = sat.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.symbols is None
    assert args.entry_fill_model == "current"
    assert args.max_holding_days is None


# --- 러너: true-rerun 잠금 / 브로커 미사용 / shadow 추가 sim 없음 ---


def test_runner_locked_params_no_broker(monkeypatch):
    monkeypatch.setattr(sat.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "AMD": object(), "MU": object(), "AAPL": object()}, None))
    monkeypatch.setattr(sat.run_sim, "_final_marks", lambda a, r: {})

    legs = (_leg("MU", 300.0), _leg("AMD", 200.0), _leg("NVDA", 100.0))
    monkeypatch.setattr(sat, "compute_trade_diagnostics",
                        lambda md, final_prices=None: SimpleNamespace(trades=legs))
    rows = tuple(SimpleNamespace(symbol=l.symbol, context_date=l.entry_date,
                                 snapshot=_snap(mom=0.5, vol=1.2, up=True)) for l in legs)
    monkeypatch.setattr(sat, "compute_feature_diagnostics",
                        lambda md, pd, benchmark_prices=None, source_trades=None: SimpleNamespace(rows=rows))
    monkeypatch.setattr(sat, "compute_baseline_comparison",
                        lambda perf, pd, **k: SimpleNamespace(baselines=(
                            SimpleNamespace(name="SPY buy-hold", cumulative_return=0.4),
                            SimpleNamespace(name="QQQ buy-hold", cumulative_return=0.55),
                            SimpleNamespace(name="equal-weight", cumulative_return=1.2))))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), args.stop_loss_pct, args.trailing_stop_pct,
                         args.max_holding_days, tuple(args.symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(), real_orders_placed=0)

    report, error = sat.run_signal_ablation(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    assert isinstance(report, AblationReport)
    assert report.real_orders_placed == 0

    # baseline + 7 true-rerun = 8 sim(shadow는 추가 sim 없음).
    assert len(captured) == 8
    for fill, buf, sm, wk, stop, trail, hold, syms in captured:
        assert fill == "next-bar-limit" and buf == 0.03 and sm == "fractional" and wk == ()
        assert sat.LEVERAGED_ETFS.isdisjoint(syms)
        assert set(syms) <= {"NVDA", "AMD", "MU", "AAPL"}
    combos = {(stop, trail, hold) for *_, stop, trail, hold, _ in captured}
    assert (None, None, None) in combos            # no_exit_controls
    assert (None, 0.20, 60) in combos              # no_stop_loss
    assert (0.15, None, 60) in combos              # no_trailing_stop
    assert (0.15, 0.20, None) in combos            # no_time_stop

    names = [v.name for v in report.variants]
    assert "shadow_drop_low_momentum" in names and "no_exit_controls" in names
    shadow_modes = [v.mode for v in report.variants if v.name.startswith("shadow_")]
    assert shadow_modes and all(m == MODE_SHADOW for m in shadow_modes)
    # no_MU 변형은 MU를 뺀 심볼로 돌았다.
    no_mu_call = next(c for c in captured if "MU" not in c[7] and len(c[7]) == 3)
    assert set(no_mu_call[7]) == {"NVDA", "AMD", "AAPL"}
