"""Phase 5 step1 — 레짐 필터 테스트 (TDD Red→Green).

spec: specs/regime.md  ·  헌장: docs/STRATEGY.md §8
- 순수 함수: (SPY 가격 Series, VIX float) → Regime. 외부 I/O 없음.
- 4레짐(A/B/C/D) + 경계값 + fail-closed 엣지케이스.
"""

import numpy as np
import pandas as pd

from algorithms.regime import (
    Regime,
    RegimePolicy,
    classify_regime,
    policy_for,
)


def _above_200d(n: int = 260) -> pd.Series:
    """최신 종가가 200일 MA보다 확실히 위인 상승추세."""
    return pd.Series(np.linspace(80, 200, n))


def _below_200d(n: int = 260) -> pd.Series:
    """최신 종가가 200일 MA보다 확실히 아래인 하락추세."""
    return pd.Series(np.linspace(200, 80, n))


# --- 레짐 분류 ---


def test_normal_bull_spy_up_vix_low():
    assert classify_regime(_above_200d(), 15.0) == Regime.NORMAL_BULL


def test_nervous_bull_spy_up_vix_elevated():
    assert classify_regime(_above_200d(), 25.0) == Regime.NERVOUS_BULL


def test_bearish_spy_below_200d():
    # SPY < 200d → C. VIX가 낮아도 약세장이면 진입 정지.
    assert classify_regime(_below_200d(), 15.0) == Regime.BEARISH


def test_panic_vix_extreme_overrides_trend():
    # v2: VIX > 35(extreme) → D, 추세(상승)와 무관하게 최우선.
    assert classify_regime(_above_200d(), 36.0) == Regime.PANIC


def test_panic_overrides_even_in_bear():
    assert classify_regime(_below_200d(), 40.0) == Regime.PANIC


# --- v2: D 확정조건(히스테리시스) ---


def test_single_day_vix_31_is_not_panic():
    # 단발 VIX 31(1일) → D 아님(extreme 35 미만 + 2일연속 아님) → 불 구간이면 B.
    assert classify_regime(_above_200d(), 31.0) == Regime.NERVOUS_BULL


def test_two_consecutive_vix_above_30_is_panic():
    # VIX>30 이 2일 연속 → D 확정.
    assert classify_regime(_above_200d(), pd.Series([31.0, 31.0])) == Regime.PANIC


def test_one_day_above_30_then_below_is_not_panic():
    # 직전엔 31이었어도 최신이 28이면 2일연속 아님 → D 아님.
    assert classify_regime(_above_200d(), pd.Series([31.0, 28.0])) == Regime.NERVOUS_BULL


def test_single_extreme_in_series_is_panic():
    # 시리즈 최신값이 36(extreme) → D.
    assert classify_regime(_above_200d(), pd.Series([15.0, 36.0])) == Regime.PANIC


# --- 경계값 ---


def test_vix_exactly_30_is_not_panic():
    # VIX == 30 은 > 30 이 아니므로 PANIC 아님 → 불 구간이면 B.
    assert classify_regime(_above_200d(), 30.0) == Regime.NERVOUS_BULL


def test_vix_exactly_20_is_nervous_bull():
    # VIX == 20 은 < 20 이 아니므로 A 아님 → B.
    assert classify_regime(_above_200d(), 20.0) == Regime.NERVOUS_BULL


def test_spy_exactly_at_200d_is_not_bearish():
    # 모든 가격 동일 → 최신가 == 200d MA → BEARISH 아님 → 불 구간.
    flat = pd.Series([100.0] * 260)
    assert classify_regime(flat, 15.0) == Regime.NORMAL_BULL


# --- fail-closed 엣지케이스 ---


def test_vix_none_is_panic():
    assert classify_regime(_above_200d(), None) == Regime.PANIC


def test_vix_nan_is_panic():
    assert classify_regime(_above_200d(), float("nan")) == Regime.PANIC


def test_spy_insufficient_data_is_bearish():
    # 200d MA 계산 불가 → 상승추세 확인 불가 → 진입 불가(C).
    assert classify_regime(_above_200d(n=120), 15.0) == Regime.BEARISH


def test_custom_thresholds_are_parameterized():
    # v2: vix_extreme를 낮추면 같은 단발 VIX가 PANIC이 된다.
    assert classify_regime(_above_200d(), 25.0, vix_extreme=24.0) == Regime.PANIC
    # panic_consecutive_days를 1로 낮추면 단발 31(>30)도 D.
    assert (
        classify_regime(_above_200d(), 31.0, panic_consecutive_days=1) == Regime.PANIC
    )
    # ma_period를 줄이면 짧은 데이터로도 추세 판정 가능.
    assert classify_regime(_above_200d(n=120), 15.0, ma_period=100) == Regime.NORMAL_BULL


# --- policy_for ---


def test_policy_normal_bull():
    p = policy_for(Regime.NORMAL_BULL)
    assert isinstance(p, RegimePolicy)
    assert p.allow_new_entry is True
    assert p.size_multiplier == 1.0
    assert p.exit_fraction_on_break == 0.0


def test_policy_nervous_bull_reduces_size():
    p = policy_for(Regime.NERVOUS_BULL)
    assert p.allow_new_entry is True
    assert p.size_multiplier == 0.5


def test_policy_bearish_blocks_entry_no_forced_exit():
    # v2: C는 강제청산 제거(0.5→0.0). 신규만 막고 기존은 개별 스탑/트레일로 관리.
    p = policy_for(Regime.BEARISH)
    assert p.allow_new_entry is False
    assert p.size_multiplier == 0.0
    assert p.exit_fraction_on_break == 0.0


def test_policy_panic_blocks_entry_and_full_exit():
    p = policy_for(Regime.PANIC)
    assert p.allow_new_entry is False
    assert p.size_multiplier == 0.0
    assert p.exit_fraction_on_break == 1.0


def test_cd_regimes_never_allow_new_entry():
    for regime in (Regime.BEARISH, Regime.PANIC):
        assert policy_for(regime).allow_new_entry is False
