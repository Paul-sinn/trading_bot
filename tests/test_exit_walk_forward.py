"""exit_walk_forward 테스트 (spec: specs/exit_walk_forward.md).

후보 청산 정책 워크포워드 검증(실험 전용). 청산 플래그+날짜만 바꾼 true-rerun. 진입/유니버스/베이스라인·
프로덕션 로직 불변. 베이스라인 승격 없음. out-of-bull 미가용 마킹. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.exit_deep_dive import summarize_variant
from agents.exit_walk_forward import (
    ExitWalkForwardReport,
    PolicyWindow,
    build_exit_walk_forward,
    compute_stability_verdict,
    compute_window_compares,
    format_exit_walk_forward_markdown,
    generate_exit_windows,
)
from agents.trade_diagnostics import TradeLeg
from agents.walk_forward import Window

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.exit_candidate_walk_forward as ecwf  # noqa: E402


def _leg(symbol, pnl):
    return TradeLeg(symbol=symbol, entry_date="2025-01-02", exit_date="2025-03-03", entry_price=100.0,
                    exit_price=100.0 + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / 100, exit_reason="time_stop")


def _perf(ret, mdd, trades, pnl):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=0.6,
                           total_pnl=pnl, num_trades=trades)


def _result(ret, mdd, pnl, trades=5):
    legs = [_leg("MU", pnl)] if trades else []
    return summarize_variant("p", (0.15, 0.20, 60), legs, _perf(ret, mdd, trades, pnl), spy=0.4, qqq=0.55)


def _pw(policy, label, kind, *, ret, mdd, pnl, trades=5, start="2025-01-01", end="2025-12-31"):
    return PolicyWindow(label=label, kind=kind, start=start, end=end, policy=policy,
                        result=_result(ret, mdd, pnl, trades), eq_return=1.2)


# --- 윈도우 생성 ---


def test_generate_exit_windows_kinds_and_bounds():
    wins = generate_exit_windows("2025-01-01", "2026-06-30")
    kinds = {w.kind for w in wins}
    assert kinds == {"year", "quarter", "roll3", "roll6", "roll12"}
    assert {w.label for w in wins if w.kind == "year"} == {"2025", "2026"}
    for w in wins:
        if w.start and w.end:
            assert w.start <= w.end


def test_generate_exit_windows_roll12_needs_full_year():
    wins = generate_exit_windows("2025-01-01", "2025-08-31")    # 8개월
    assert not [w for w in wins if w.kind == "roll12"]          # 12m 안 들어감
    assert [w for w in wins if w.kind == "quarter"]


# --- 비교 / 안정성 ---


def test_window_compares_beats_flags():
    base = [_pw("locked_baseline", "2025-Q2", "quarter", ret=0.1, mdd=0.1, pnl=100.0)]
    cand = [_pw("candidate", "2025-Q2", "quarter", ret=0.2, mdd=0.12, pnl=150.0)]
    c = compute_window_compares(base, cand)[0]
    assert c.cand_beats_pnl is True            # 150 > 100
    assert c.cand_beats_ratio is True          # 0.2/0.12 > 0.1/0.1
    assert c.cand_worse_mdd is True            # 0.12 > 0.10
    assert c.pnl_advantage == 50.0


def test_stability_verdict_counts_and_concentration():
    base = [_pw("locked_baseline", lab, "quarter", ret=0.1, mdd=0.1, pnl=100.0)
            for lab in ("Q1", "Q2", "Q3")]
    # 후보가 Q1에서만 크게 이김(집중), Q2/Q3는 소폭/패.
    cand = [_pw("candidate", "Q1", "quarter", ret=0.5, mdd=0.1, pnl=500.0),
            _pw("candidate", "Q2", "quarter", ret=0.09, mdd=0.12, pnl=90.0),
            _pw("candidate", "Q3", "quarter", ret=0.11, mdd=0.1, pnl=105.0)]
    v = compute_stability_verdict(compute_window_compares(base, cand))
    assert v.n_windows == 3
    assert v.cand_beats_pnl == 2                # Q1, Q3
    assert v.advantage_concentrated is True     # Q1 우위가 대부분
    assert v.best_window.label == "Q1"


# --- build / out-of-bull / format ---


def test_build_marks_out_of_bull_not_available():
    pw = [_pw("locked_baseline", "2025-Q2", "quarter", ret=0.1, mdd=0.1, pnl=100.0, start="2025-04-01", end="2025-06-30"),
          _pw("candidate", "2025-Q2", "quarter", ret=0.2, mdd=0.1, pnl=150.0, start="2025-04-01", end="2025-06-30")]
    compares = compute_window_compares([pw[0]], [pw[1]])
    rep = build_exit_walk_forward(pw, compares, compute_stability_verdict(compares),
                                  data_start="2025-01-01", data_end="2026-06-18")
    assert rep.out_of_bull == "NOT_AVAILABLE"
    assert "insufficient local data history" in rep.out_of_bull_reason
    assert rep.real_orders_placed == 0


def test_build_available_when_pre_bull_window_trades():
    pw = [_pw("locked_baseline", "2023", "year", ret=0.1, mdd=0.1, pnl=100.0, start="2023-01-01", end="2023-12-31")]
    rep = build_exit_walk_forward(pw, (), compute_stability_verdict(()),
                                  data_start="2023-01-01", data_end="2026-06-18")
    assert rep.out_of_bull == "AVAILABLE"


def test_format_has_verdict_and_no_promotion():
    base = [_pw("locked_baseline", "2025-Q2", "quarter", ret=0.1, mdd=0.1, pnl=100.0)]
    cand = [_pw("candidate", "2025-Q2", "quarter", ret=0.2, mdd=0.1, pnl=150.0)]
    compares = compute_window_compares(base, cand)
    rep = build_exit_walk_forward(base + cand, compares, compute_stability_verdict(compares),
                                  data_start="2025-01-01", data_end="2026-06-18")
    md = format_exit_walk_forward_markdown(rep)
    assert "Exit Candidate Walk-Forward Validation" in md
    assert "OUT_OF_BULL_VALIDATION = NOT_AVAILABLE" in md
    assert "베이스라인 승격 없음" in md or "승격할 증거가 충분한가?** **아니오" in md
    assert "안정성 판정" in md
    assert "real_orders_placed = 0" in md


# --- 상수/기본값 잠금 ---


def test_locked_entry_constants_and_policies():
    assert ecwf._FILL_MODEL == "next-bar-limit" and ecwf._BUFFER == 0.03 and ecwf._SHARE_MODE == "fractional"
    assert ecwf._POLICIES[0][:4] == ("locked_baseline", 0.15, 0.20, 60)
    assert ecwf._POLICIES[1][:4] == ("candidate", 0.15, None, 45)
    import experiments.universe_bias_test as ubt
    assert ecwf.BASELINE_UNIVERSE is ubt.BASELINE_UNIVERSE


def test_run_sim_defaults_unchanged():
    args = ecwf.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


# --- 러너: 진입 잠금 / 청산+날짜만 변형 / 브로커 미사용 ---


def test_runner_entry_locked_no_broker(monkeypatch):
    monkeypatch.setattr(ecwf.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "AMD": object(), "MU": object()}, None))
    monkeypatch.setattr(ecwf.run_sim, "_final_marks", lambda a, r: {})
    monkeypatch.setattr(ecwf, "compute_trade_diagnostics",
                        lambda md, final_prices=None: SimpleNamespace(trades=(_leg("MU", 100.0),)))
    monkeypatch.setattr(ecwf, "compute_baseline_comparison",
                        lambda perf, pd, **k: SimpleNamespace(baselines=(
                            SimpleNamespace(name="SPY buy-hold", cumulative_return=0.4),
                            SimpleNamespace(name="QQQ buy-hold", cumulative_return=0.55),
                            SimpleNamespace(name="equal-weight", cumulative_return=1.2))))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), args.stop_loss_pct, args.trailing_stop_pct,
                         args.max_holding_days, args.start_date, args.end_date, tuple(args.symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(0.5, 0.1, 5, 200.0), real_orders_placed=0)

    wins = [Window("2025", "year", "2025-01-01", "2025-12-31"),
            Window("2026", "year", "2026-01-01", "2026-06-18")]
    report, error = ecwf.run_exit_candidate_walk_forward(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake, windows=wins)
    assert error is None
    assert isinstance(report, ExitWalkForwardReport)
    assert report.real_orders_placed == 0

    for fill, buf, sm, wk, stop, trail, hold, start, end, syms in captured:
        assert fill == "next-bar-limit" and buf == 0.03 and sm == "fractional" and wk == ()
        assert set(syms) == {"NVDA", "AMD", "MU"}
        assert start in ("2025-01-01", "2026-01-01") and end in ("2025-12-31", "2026-06-18")
    combos = {(stop, trail, hold) for *_, stop, trail, hold, _, _, _ in captured}
    assert (0.15, 0.20, 60) in combos and (0.15, None, 45) in combos and (0.20, None, 60) in combos
    # 2 윈도우 × 3 정책 = 6 sim.
    assert len(captured) == 6
    assert {p.policy for p in report.policy_windows} == {"locked_baseline", "candidate", "alt_candidate"}
