"""exit_deep_dive 테스트 (spec: specs/exit_deep_dive.md).

청산 정책/트레일링 딥다이브(실험 전용). 청산 플래그만 변형하는 true-rerun. 진입/유니버스/베이스라인·
프로덕션 로직 불변. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.exit_deep_dive import (
    ExitDeepDive,
    build_exit_deep_dive,
    compute_holding_days,
    exit_reason_breakdown,
    format_exit_deep_dive_markdown,
    summarize_variant,
    trailing_impact,
)
from agents.trade_diagnostics import TradeLeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.exit_policy_deep_dive as edd  # noqa: E402


def _leg(symbol, pnl, *, entry="2025-01-02", exit="2025-03-03", reason="time_stop"):
    return TradeLeg(symbol=symbol, entry_date=entry, exit_date=exit, entry_price=100.0,
                    exit_price=100.0 + (pnl or 0), qty=1.0, pnl=pnl, pnl_pct=(None if pnl is None else pnl / 100),
                    exit_reason=reason)


def _perf(ret=0.75, mdd=0.13, win=0.65, pnl=750.0, trades=80):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


# --- 홀딩일 / 청산 사유 ---


def test_compute_holding_days_closed_only():
    legs = [_leg("A", 10, entry="2025-01-01", exit="2025-01-31"),    # 30일
            _leg("B", 20, entry="2025-01-01", exit="2025-03-02"),    # 60일
            _leg("C", None, exit=None, reason="OPEN")]               # 미청산 제외
    assert compute_holding_days(legs) == 45.0


def test_exit_reason_breakdown_counts_and_avg():
    legs = [_leg("A", 10, reason="time_stop"), _leg("B", 30, reason="time_stop"),
            _leg("C", -5, reason="trailing_stop_hit"), _leg("D", -8, reason="stop_loss_hit"),
            _leg("E", 4, reason="manual_sim_exit"), _leg("F", None, exit=None, reason="OPEN")]
    by = {e.reason: e for e in exit_reason_breakdown(legs)}
    assert by["time_stop"].count == 2 and by["time_stop"].total_pnl == 40.0
    assert by["time_stop"].avg_pnl == 20.0
    assert by["trailing_stop"].count == 1
    assert by["other"].count == 1                       # manual_sim_exit
    assert by["open"].count == 1 and by["open"].avg_pnl is None   # 무가 leg


# --- 트레일링 영향 ---


def test_trailing_impact_hurt_and_helped():
    base = [_leg("MU", 100.0), _leg("AMD", 50.0), _leg("NVDA", 80.0)]
    no_trail = [_leg("MU", 200.0), _leg("AMD", 40.0), _leg("NVDA", 80.0)]
    hurt, helped = trailing_impact(base, no_trail)
    assert hurt[0].symbol == "MU" and hurt[0].delta == 100.0      # trail_off > baseline → 트레일링이 깎음
    assert helped[0].symbol == "AMD" and helped[0].delta == -10.0  # 트레일링이 보호


# --- summarize / build ---


def test_summarize_variant_metrics():
    legs = [_leg("MU", 200.0), _leg("AMD", 100.0), _leg("NVDA", -40.0)]
    r = summarize_variant("baseline", (0.15, 0.20, 60), legs, _perf(ret=0.75, mdd=0.15),
                          spy=0.4, qqq=0.55)
    assert r.stop == 0.15 and r.trail == 0.20 and r.max_hold == 60
    assert r.return_over_mdd == 0.75 / 0.15
    assert r.avg_holding_days == 60.0
    assert r.top1_symbol == "MU"
    assert r.beats_spy is True and r.beats_qqq is True


def test_build_excludes_diagnostic_from_best():
    base = summarize_variant("baseline", (0.15, 0.20, 60), [_leg("MU", 200.0)], _perf(ret=0.6, mdd=0.2))
    good = summarize_variant("trail_off", (0.15, None, 60), [_leg("MU", 300.0)], _perf(ret=0.9, mdd=0.15))
    diag = summarize_variant("all_exits_off", (None, None, None), [_leg("MU", 999.0)],
                             _perf(ret=9.9, mdd=0.05, pnl=9990.0), diagnostic_only=True)
    rep = build_exit_deep_dive([base, good, diag], (), ())
    # diagnostic이 ret/MDD·PnL 최고지만 best에서 제외.
    assert rep.best_by_ratio == "trail_off"
    assert rep.best_by_pnl == "trail_off"
    assert any("diagnostic only" in w for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_format_has_methodology_and_open_handling():
    base = summarize_variant("baseline", (0.15, 0.20, 60), [_leg("MU", 200.0, reason="time_stop")],
                             _perf(), spy=0.4, qqq=0.55)
    trail_off = summarize_variant("trail_off", (0.15, None, 60), [_leg("MU", 300.0)], _perf(pnl=900.0))
    diag = summarize_variant("all_exits_off", (None, None, None), [_leg("MU", 999.0)], _perf(),
                             diagnostic_only=True)
    md = format_exit_deep_dive_markdown(build_exit_deep_dive([base, trail_off, diag], (), ()))
    assert "Exit Policy / Trailing Stop Deep Dive" in md
    assert "방법론" in md and "마지막 종가로 마크" in md          # 미청산 처리 설명
    assert "diagnostic only" in md or "*(diag)*" in md
    assert "잠긴 베이스라인 유지" in md
    assert "real_orders_placed = 0" in md


# --- 상수/기본값 잠금 ---


def test_locked_entry_constants_and_universe():
    assert edd._FILL_MODEL == "next-bar-limit" and edd._BUFFER == 0.03
    assert edd._STOP == 0.15 and edd._TRAIL == 0.20 and edd._MAX_HOLD == 60 and edd._SHARE_MODE == "fractional"
    import experiments.universe_bias_test as ubt
    assert edd.BASELINE_UNIVERSE is ubt.BASELINE_UNIVERSE


def test_run_sim_defaults_unchanged():
    args = edd.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


# --- 러너: 진입 잠금 / 청산만 변형 / 격리 / 브로커 미사용 ---


def test_runner_entry_locked_exits_isolated_no_broker(monkeypatch):
    monkeypatch.setattr(edd.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "AMD": object(), "MU": object()}, None))
    monkeypatch.setattr(edd.run_sim, "_final_marks", lambda a, r: {})
    legs = (_leg("MU", 200.0), _leg("AMD", 100.0), _leg("NVDA", 50.0))
    monkeypatch.setattr(edd, "compute_trade_diagnostics",
                        lambda md, final_prices=None: SimpleNamespace(trades=legs))
    monkeypatch.setattr(edd, "compute_baseline_comparison",
                        lambda perf, pd, **k: SimpleNamespace(baselines=(
                            SimpleNamespace(name="SPY buy-hold", cumulative_return=0.4),
                            SimpleNamespace(name="QQQ buy-hold", cumulative_return=0.55))))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), args.stop_loss_pct, args.trailing_stop_pct,
                         args.max_holding_days, tuple(args.symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(), real_orders_placed=0)

    report, error = edd.run_exit_deep_dive(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    assert isinstance(report, ExitDeepDive)
    assert report.real_orders_placed == 0

    # 진입 파라미터는 모든 변형에서 고정, 심볼 동일, 청산만 변형.
    for fill, buf, sm, wk, stop, trail, hold, syms in captured:
        assert fill == "next-bar-limit" and buf == 0.03 and sm == "fractional" and wk == ()
        assert set(syms) == {"NVDA", "AMD", "MU"}
    combos = {(stop, trail, hold) for *_, stop, trail, hold, _ in captured}
    assert (0.15, 0.20, 60) in combos and (0.15, None, 60) in combos     # baseline / trail_off
    assert (None, None, None) in combos                                  # all_exits_off diagnostic
    assert (0.15, 0.30, 60) in combos and (0.15, 0.10, 60) in combos     # trailing sweep
    # 캐싱: 16개 변형이지만 중복 파라미터는 합쳐져 sim 호출이 더 적다.
    assert len(captured) < 16
    names = [v.name for v in report.variants]
    assert "all_exits_off" in names and any(v.diagnostic_only for v in report.variants)
    assert names.count("baseline") == 1 and len(names) == 16
