"""Phase 5 step0 — 시계열 모멘텀·추세 신호 테스트 (TDD Red→Green).

spec: specs/signals.md  ·  헌장: docs/STRATEGY.md §1
- 순수 함수 (입력 Series/DataFrame → TrendState/Signal/float). 외부 I/O 없음.
- 기존 EMA/RSI/MACD 다수결은 폐기 — 추세 기반 overall + 상대강도 + RSI 원시값.
- 회귀: RSI 과매도 단독은 overall BULLISH를 만들지 않는다(헌장 §1).
"""

import numpy as np
import pandas as pd

from algorithms.signals import (
    Signal,
    SignalResult,
    TrendState,
    generate_signals,
    relative_strength,
    rsi_value,
    trend_state,
)


def _uptrend(n: int = 260, start: float = 80.0, end: float = 200.0) -> pd.Series:
    """종가 > 50d MA > 200d MA 가 성립하는 명백한 상승추세."""
    return pd.Series(np.linspace(start, end, n))


def _downtrend(n: int = 260, start: float = 200.0, end: float = 80.0) -> pd.Series:
    return pd.Series(np.linspace(start, end, n))


# --- trend_state ---


def test_trend_state_clear_uptrend_is_up():
    assert trend_state(_uptrend()) == TrendState.UP


def test_trend_state_clear_downtrend_is_down():
    assert trend_state(_downtrend()) == TrendState.DOWN


def test_trend_state_choppy_sideways_is_neutral():
    # 횡보(사인파) → MA 정렬 불성립 → NEUTRAL.
    x = np.linspace(0, 8 * np.pi, 260)
    prices = pd.Series(100 + 5 * np.sin(x))
    assert trend_state(prices) == TrendState.NEUTRAL


def test_trend_state_insufficient_data_is_neutral():
    # slow(200) 미만 → 워밍업 전 → NEUTRAL.
    assert trend_state(_uptrend(n=120)) == TrendState.NEUTRAL


def test_trend_state_flat_prices_is_neutral():
    assert trend_state(pd.Series([100.0] * 260)) == TrendState.NEUTRAL


def test_trend_state_empty_is_neutral():
    assert trend_state(pd.Series([], dtype=float)) == TrendState.NEUTRAL


# --- relative_strength ---


def test_relative_strength_stronger_than_benchmark_is_true():
    asset = pd.Series(np.linspace(100, 160, 100))      # +60%
    bench = pd.Series(np.linspace(100, 110, 100))      # +10%
    assert relative_strength(asset, bench, lookback=63) is True


def test_relative_strength_weaker_than_benchmark_is_false():
    asset = pd.Series(np.linspace(100, 105, 100))      # +5%
    bench = pd.Series(np.linspace(100, 130, 100))      # +30%
    assert relative_strength(asset, bench, lookback=63) is False


def test_relative_strength_insufficient_data_is_none():
    asset = pd.Series(np.linspace(100, 110, 30))
    bench = pd.Series(np.linspace(100, 105, 30))
    assert relative_strength(asset, bench, lookback=63) is None


# --- rsi_value (원시값, Signal 아님) ---


def test_rsi_value_returns_float_in_range():
    rsi = rsi_value(pd.Series(np.linspace(100, 160, 60)))
    assert isinstance(rsi, float)
    assert 0.0 <= rsi <= 100.0


def test_rsi_value_monotonic_rise_is_high():
    assert rsi_value(pd.Series(np.linspace(100, 200, 60))) > 70.0


def test_rsi_value_monotonic_fall_is_low():
    assert rsi_value(pd.Series(np.linspace(200, 100, 60))) < 30.0


def test_rsi_value_flat_is_fifty():
    assert rsi_value(pd.Series([100.0] * 60)) == 50.0


def test_rsi_value_insufficient_data_is_none():
    assert rsi_value(pd.Series([100.0, 101.0])) is None


# --- generate_signals ---


def test_generate_signals_uptrend_overall_bullish():
    df = pd.DataFrame({"close": _uptrend()})
    result = generate_signals(df)
    assert isinstance(result, SignalResult)
    assert result.trend == TrendState.UP
    assert result.overall == Signal.BULLISH


def test_generate_signals_downtrend_overall_bearish():
    df = pd.DataFrame({"close": _downtrend()})
    result = generate_signals(df)
    assert result.trend == TrendState.DOWN
    assert result.overall == Signal.BEARISH


def test_generate_signals_flat_neutral_overall():
    df = pd.DataFrame({"close": [100.0] * 260})
    assert generate_signals(df).overall == Signal.NEUTRAL


def test_generate_signals_with_benchmark_sets_relative_strength():
    df = pd.DataFrame({"close": _uptrend()})
    bench = pd.DataFrame({"close": pd.Series(np.linspace(100, 110, 260))})
    result = generate_signals(df, benchmark=bench)
    assert result.relative_strength is True


def test_generate_signals_without_benchmark_relative_strength_none():
    df = pd.DataFrame({"close": _uptrend()})
    assert generate_signals(df).relative_strength is None


def test_generate_signals_exposes_rsi_value():
    df = pd.DataFrame({"close": _uptrend()})
    result = generate_signals(df)
    assert result.rsi is None or isinstance(result.rsi, float)


def test_generate_signals_missing_column_raises():
    df = pd.DataFrame({"price": [1.0, 2.0, 3.0]})
    try:
        generate_signals(df)
    except (KeyError, ValueError):
        pass
    else:
        raise AssertionError("missing price column should raise")


# --- 회귀: 헌장 §1 패러다임 해소 ---


def test_rsi_oversold_alone_does_not_make_overall_bullish():
    """단조 하락 → RSI 과매도지만 추세는 DOWN → overall은 절대 BULLISH 아님.

    기존 다수결 설계에서는 RSI 과매도가 BULLISH 표를 던졌다. 헌장 §1은 이를 금지한다.
    """
    df = pd.DataFrame({"close": _downtrend()})
    result = generate_signals(df)
    assert result.overall != Signal.BULLISH
    # RSI 원시값은 낮게(과매도) 나오더라도 매수신호로 승격되지 않는다.
    assert result.rsi is None or result.rsi < 50.0


# --- 엣지: NaN ---


def test_nan_values_handled_without_error():
    base = list(np.linspace(80, 200, 260))
    base[5] = np.nan
    base[100] = np.nan
    prices = pd.Series(base)
    assert isinstance(trend_state(prices), TrendState)
    r = rsi_value(prices)
    assert r is None or isinstance(r, float)


def test_all_nan_is_neutral():
    prices = pd.Series([np.nan] * 260)
    assert trend_state(prices) == TrendState.NEUTRAL
    assert rsi_value(prices) is None
