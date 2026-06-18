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


def test_panic_vix_above_30_overrides_trend():
    # VIX > 30 → D, 추세(상승)와 무관하게 최우선.
    assert classify_regime(_above_200d(), 35.0) == Regime.PANIC


def test_panic_overrides_even_in_bear():
    assert classify_regime(_below_200d(), 40.0) == Regime.PANIC


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
    # 임계값을 파라미터로 노출 — vix_panic을 낮추면 같은 VIX가 PANIC이 된다.
    assert classify_regime(_above_200d(), 25.0, vix_panic=24.0) == Regime.PANIC
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


def test_policy_bearish_blocks_entry_and_partial_exit():
    p = policy_for(Regime.BEARISH)
    assert p.allow_new_entry is False
    assert p.size_multiplier == 0.0
    assert p.exit_fraction_on_break == 0.5


def test_policy_panic_blocks_entry_and_full_exit():
    p = policy_for(Regime.PANIC)
    assert p.allow_new_entry is False
    assert p.size_multiplier == 0.0
    assert p.exit_fraction_on_break == 1.0


def test_cd_regimes_never_allow_new_entry():
    for regime in (Regime.BEARISH, Regime.PANIC):
        assert policy_for(regime).allow_new_entry is False
