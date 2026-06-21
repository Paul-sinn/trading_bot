"""exit_candidate 테스트 (spec: specs/exit_candidate.md).

후보 청산 정책 검증(실험 전용). 청산 플래그만 바꾼 true-rerun. 진입/유니버스/베이스라인·프로덕션 로직
불변. 베이스라인 승격 없음. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

from agents.exit_candidate import (
    CandidateValidationReport,
    DropResult,
    build_candidate_validation,
    format_candidate_validation_markdown,
    make_drop,
    positive_active_quarters,
    yearly_pnl,
)
from agents.exit_deep_dive import summarize_variant
from agents.trade_diagnostics import TradeLeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.exit_candidate_validation as ecv  # noqa: E402


def _leg(symbol, pnl, *, entry="2025-01-02", exit="2025-03-03"):
    return TradeLeg(symbol=symbol, entry_date=entry, exit_date=exit, entry_price=100.0,
                    exit_price=100.0 + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / 100, exit_reason="time_stop")


def _perf(ret=0.75, mdd=0.13, win=0.65, pnl=750.0, trades=80):
    return SimpleNamespace(cumulative_return=ret, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades)


def _slip(slip, pnl):
    return SimpleNamespace(slippage=slip, total_pnl=pnl, return_pct=pnl / 1000.0)


def _policy(name, params, legs, perf, *, slippage=(), no_arm=None, eq=1.2):
    full = summarize_variant(name, params, legs, perf, spy=0.4, qqq=0.55)
    return SimpleNamespace(  # PolicyValidation 호환(format/build가 읽는 필드)
        name=name, stop=params[0], trail=params[1], max_hold=params[2], full=full, eq_return=eq,
        yearly=yearly_pnl(legs), positive_quarters=positive_active_quarters(full.quarterly)[0],
        active_quarters=positive_active_quarters(full.quarterly)[1],
        slippage=tuple(slippage), loo=(), worst_drop=None, no_mu=None, no_arm=no_arm, no_top3=None)


# --- 순수 helper ---


def test_yearly_pnl_groups_by_exit_year():
    legs = [_leg("A", 10, exit="2025-03-03"), _leg("B", 20, exit="2026-02-02"),
            _leg("C", 5, exit="2025-05-05")]
    y = dict(yearly_pnl(legs))
    assert y["2025"] == 15.0 and y["2026"] == 20.0


def test_positive_active_quarters():
    pos, active = positive_active_quarters([("2025-Q1", 50.0), ("2025-Q2", -5.0),
                                            ("2025-Q3", 0.0), ("2025-Q4", 30.0)])
    assert active == 3 and pos == 2          # 0.0 분기는 비활성


def test_make_drop_delta():
    d = make_drop("no_MU", full_pnl=750.0, drop_pnl=500.0, drop_return=0.5)
    assert d.delta_pnl == -250.0 and d.cumulative_return == 0.5


# --- build / 경고 ---


def test_build_flags_no_promotion_and_slippage_loss():
    base = _policy("locked_baseline", (0.15, 0.20, 60), [_leg("MU", 400.0), _leg("AMD", 350.0)],
                   _perf(ret=0.78, mdd=0.13, pnl=778.0), slippage=(_slip(0.005, 760.0), _slip(0.01, 740.0)))
    cand = _policy("candidate", (0.15, None, 45), [_leg("MU", 450.0), _leg("AMD", 350.0)],
                   _perf(ret=0.90, mdd=0.20, pnl=800.0),     # ret/MDD 4.5 < base 6.0
                   slippage=(_slip(0.005, 700.0), _slip(0.01, 680.0)),
                   no_arm=DropResult("no_ARM", 500.0, 0.5, -300.0))   # ARM 제거 -38%
    rep = build_candidate_validation([base, cand], baseline_name="locked_baseline",
                                     candidate_name="candidate")
    assert any("no promotion" in w for w in rep.warnings)
    assert any("슬리피지" in w for w in rep.warnings)         # 후보가 슬리피지 후 못 이김
    assert any("ret/MDD 개선 아님" in w for w in rep.warnings)
    assert any("ARM 쏠림" in w for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_format_says_no_promotion_and_evidence():
    base = _policy("locked_baseline", (0.15, 0.20, 60), [_leg("MU", 400.0)], _perf(),
                   slippage=(_slip(0.005, 760.0), _slip(0.01, 740.0)))
    cand = _policy("candidate", (0.15, None, 45), [_leg("MU", 450.0)], _perf(pnl=900.0),
                   slippage=(_slip(0.005, 880.0), _slip(0.01, 860.0)),
                   no_arm=DropResult("no_ARM", 800.0, 0.8, -100.0))
    md = format_candidate_validation_markdown(
        build_candidate_validation([base, cand], baseline_name="locked_baseline", candidate_name="candidate"))
    assert "Candidate Exit Policy Validation" in md
    assert "베이스라인 승격 없음" in md
    assert "승격 전 필요한 증거" in md
    assert "real_orders_placed = 0" in md


# --- 상수/기본값 잠금 ---


def test_locked_entry_constants_and_universe():
    assert ecv._FILL_MODEL == "next-bar-limit" and ecv._BUFFER == 0.03 and ecv._SHARE_MODE == "fractional"
    names = [p[0] for p in ecv._POLICIES]
    assert names[0] == "locked_baseline" and "candidate" in names
    assert ecv._POLICIES[0][1:4] == (0.15, 0.20, 60)         # baseline 잠금
    assert ecv._POLICIES[1][1:4] == (0.15, None, 45)         # 후보 hold_45_trailoff
    import experiments.universe_bias_test as ubt
    assert ecv.BASELINE_UNIVERSE is ubt.BASELINE_UNIVERSE


def test_run_sim_defaults_unchanged():
    args = ecv.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


# --- 러너: 진입 잠금 / 청산만 변형 / LOO 심볼 제거 / 브로커 미사용 ---


def test_runner_locked_entry_loo_and_no_broker(monkeypatch):
    monkeypatch.setattr(ecv.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "AMD": object(), "MU": object(), "ARM": object()}, None))
    monkeypatch.setattr(ecv.run_sim, "_final_marks", lambda a, r: {})
    legs = (_leg("MU", 200.0), _leg("AMD", 100.0), _leg("NVDA", 80.0), _leg("ARM", 60.0))
    monkeypatch.setattr(ecv, "compute_trade_diagnostics",
                        lambda md, final_prices=None: SimpleNamespace(trades=legs))
    monkeypatch.setattr(ecv, "compute_baseline_comparison",
                        lambda perf, pd, **k: SimpleNamespace(baselines=(
                            SimpleNamespace(name="SPY buy-hold", cumulative_return=0.4),
                            SimpleNamespace(name="QQQ buy-hold", cumulative_return=0.55),
                            SimpleNamespace(name="equal-weight", cumulative_return=1.2))))
    captured = []

    def _fake(args):
        captured.append((args.entry_fill_model, args.entry_limit_buffer_pct, args.share_mode,
                         tuple(args.weekend_exit_symbols), args.stop_loss_pct, args.trailing_stop_pct,
                         args.max_holding_days, frozenset(args.symbols)))
        md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
        return SimpleNamespace(multiday=md, performance=_perf(), real_orders_placed=0)

    report, error = ecv.run_exit_candidate_validation(
        data_root="x", events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    assert isinstance(report, CandidateValidationReport)
    assert report.real_orders_placed == 0

    # 진입 파라미터는 모든 호출에서 고정.
    for fill, buf, sm, wk, *_ in captured:
        assert fill == "next-bar-limit" and buf == 0.03 and sm == "fractional" and wk == ()
    combos = {(stop, trail, hold) for *_, stop, trail, hold, _ in captured}
    assert (0.15, 0.20, 60) in combos and (0.15, None, 45) in combos and (0.20, None, 60) in combos
    # LOO/no_MU/no_ARM 심볼 제거가 올바른지: MU 빠진 호출, ARM 빠진 호출 존재.
    full_set = {"NVDA", "AMD", "MU", "ARM"}
    assert any(s == full_set - {"MU"} for *_, s in captured)
    assert any(s == full_set - {"ARM"} for *_, s in captured)
    names = [p.name for p in report.policies]
    assert names == ["locked_baseline", "candidate", "alt_candidate"]
