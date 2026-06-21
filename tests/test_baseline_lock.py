"""베이스라인 잠금 회귀 테스트.

원래 동작 baseline을 못 박는다: max_holding_days=60, stop 0.15, trailing 0.20,
entry_fill_model next-bar-limit, buffer 0.03, fractional. 90/120은 실험 변형 전용. 레버리지 주말청산은
opt-in·레버리지 전용(기본 빈 집합), 일반주 미적용. real_orders=0.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import experiment_matrix as em  # noqa: E402
import exit_sensitivity as es  # noqa: E402
import run_sim  # noqa: E402
import trend_leverage_experiment as tle  # noqa: E402
from agents.order_plan import NORMAL_PROFILE  # noqa: E402
from agents.sim_exit import ExitParams, ExitPolicy, ExitReason, evaluate_exit  # noqa: E402


# --- 베이스라인 설정 잠금 ---


def test_baseline_variant_config_defaults():
    c = tle.VariantConfig(name="b", data_root="x")
    assert c.max_holding_days == 60
    assert c.stop_loss_pct == 0.15
    assert c.trailing_stop_pct == 0.20
    assert c.entry_fill_model == "next-bar-limit"
    assert c.entry_limit_buffer_pct == 0.03
    assert c.share_mode == "fractional"
    assert c.weekend_exit_symbols == ()       # 일반 베이스라인은 주말청산 미적용


def test_other_runners_baseline_is_60():
    assert em.ExperimentConfig(name="b", data_root="x").max_holding_days == 60
    assert es.DEFAULT_COMBO == (0.15, 0.20, 60)
    assert NORMAL_PROFILE.max_holding_days == 60


def test_run_sim_cli_defaults_unchanged():
    args = run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.max_holding_days is None           # 기본은 청산 미적용(플래그로만)
    assert args.entry_fill_model == "current"      # 기본 진입 모델 불변
    assert args.entry_limit_buffer_pct == 0.03


# --- 90/120은 실험 변형 전용 ---


def _fake_result(pnl=260.0):
    md = SimpleNamespace(day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}))
    perf = SimpleNamespace(cumulative_return=0.26, max_drawdown=0.07, win_rate=0.55,
                           total_pnl=pnl, num_trades=48, num_closed_trades=48)
    return SimpleNamespace(performance=perf, multiday=md, real_orders_placed=0, portfolio=md.portfolio)


def test_experiment_baseline_60_variants_90_120_not_default(monkeypatch):
    monkeypatch.setattr(tle.run_sim, "_final_marks", lambda a, r: {})
    captured = []

    def _fake(args):
        captured.append((args.max_holding_days, tuple(args.weekend_exit_symbols), args.entry_fill_model))
        return _fake_result()

    rep = tle.run_trend_leverage_experiment(universe_root="x", simulate_fn=_fake)
    names = [v.name for v in rep.variants]
    assert names[0] == "baseline_realistic"
    assert captured[0][0] == 60                     # 베이스라인 = 60
    assert captured[1][0] == 90 and captured[2][0] == 120   # 90/120은 별도 변형
    # 일반 유니버스 변형은 어느 것도 주말청산을 적용하지 않는다.
    assert all(c[1] == () for c in captured)
    assert all(c[2] == "next-bar-limit" for c in captured)
    assert rep.real_orders_placed == 0


# --- 레버리지 주말청산: opt-in / 일반주 미적용 ---


def test_weekend_exit_default_off():
    p = ExitPolicy()
    assert p.weekend_exit_symbols == frozenset()
    assert p.is_active is False                     # 기본 비활성 불변
    # weekend_exit 미설정 시 강제청산 사유가 나오지 않는다.
    d = evaluate_exit(price=100.0, shares_held=10, params=ExitParams())
    assert d.should_exit is False


def test_weekend_exit_is_opt_in_and_leveraged_only():
    p = ExitPolicy(weekend_exit_symbols=frozenset({"TQQQ", "SOXL"}))
    assert p.is_active is True
    assert "NVDA" not in p.weekend_exit_symbols      # 일반주는 대상 아님
    # 명시적으로 weekend_exit=True일 때만 주말청산.
    assert evaluate_exit(price=100.0, shares_held=10,
                         params=ExitParams(weekend_exit=True)).reason == ExitReason.WEEKEND_EXIT


def test_leveraged_shadow_config_uses_strict_profile_and_weekend():
    # 러너 내부 레버리지 셰도 설정이 엄격 프로파일 + 주말청산을 쓰는지(구성 잠금).
    assert tle.LEVERAGED_SHADOW_UNIVERSE == ("TQQQ", "SOXL", "UPRO", "SQQQ")
    assert "TQQQ" in tle.LEVERAGED_ETFS and "FNGU" in tle.LEVERAGED_ETFS
