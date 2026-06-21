"""현실적 진입 베이스라인 잠금 회귀 테스트 (spec: specs/realistic_entry_baseline.md).

현실 실행 베이스라인을 next-bar-limit 3%로 못 박는다: next-open을 기본으로 승격하지 않고, 안정적인
60일/0.15/0.20/fractional 셋업을 유지하며, winner extension·gap guard는 적용하지 않고, 레버리지
주말청산은 opt-in(기본 빈 집합)으로 유지한다. 리포트/설정 전용 — real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import execution_robustness as erob  # noqa: E402
import run_sim  # noqa: E402
import trend_leverage_experiment as tle  # noqa: E402

_SETTINGS = dict(data_root="x", benchmark="SPY", symbols=None,
                 events_csv=None, assume_no_events=True, starting_cash=1000.0)


def _limit_args():
    return erob._config_to_args(_SETTINGS, "next-bar-limit", None)


# --- 현실 베이스라인 = next-bar-limit 3% ---


def test_realistic_baseline_uses_next_bar_limit():
    a = _limit_args()
    assert a.entry_fill_model == "next-bar-limit"
    assert a.entry_limit_buffer_pct == 0.03


def test_realistic_baseline_locks_stable_60day_setup():
    a = _limit_args()
    assert a.max_holding_days == 60
    assert a.stop_loss_pct == 0.15
    assert a.trailing_stop_pct == 0.20
    assert a.share_mode == "fractional"


def test_runner_locks_buffer_and_hold_constants():
    # 러너 모듈 상수 자체가 잠겨 있어야 변형이 실수로 바꾸지 못한다.
    assert erob._STOP == 0.15
    assert erob._TRAIL == 0.20
    assert erob._MAX_HOLD == 60
    assert erob._SHARE_MODE == "fractional"


# --- next-open은 어떤 기본값도 아니다 ---


def test_next_open_is_not_a_default():
    # run_sim CLI 기본은 current(참조), 실험 러너 VariantConfig 기본은 next-bar-limit.
    cli = run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert cli.entry_fill_model != "next-open"
    assert cli.entry_fill_model == "current"
    assert tle.VariantConfig(name="b", data_root="x").entry_fill_model == "next-bar-limit"
    assert tle.VariantConfig(name="b", data_root="x").entry_fill_model != "next-open"


# --- winner extension / gap guard 미적용 ---


def test_baseline_args_have_no_winner_extension_or_gap_guard():
    a = _limit_args()
    keys = set(vars(a))
    assert not any("winner" in k.lower() or "extension" in k.lower() for k in keys)
    assert not any("gap" in k.lower() for k in keys)


# --- 주말청산 opt-in / real_orders=0 ---


def test_weekend_exit_empty_by_default_and_no_live_orders():
    a = _limit_args()
    assert list(a.weekend_exit_symbols) == []
    # next-open 실험 암도 동일한 잠긴 베이스라인을 쓰되 모델만 다르다.
    n = erob._config_to_args(_SETTINGS, "next-open", None)
    assert n.max_holding_days == 60 and list(n.weekend_exit_symbols) == []
