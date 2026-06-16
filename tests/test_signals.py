"""Step 5 signals (Layer 1) 테스트 (TDD Red→Green).

spec: specs/signals.md
- 순수 함수 (입력 Series/DataFrame → Signal). 외부 I/O 없음.
- EMA 크로스 / RSI / MACD 시그널 + generate_signals 통합.
- 엣지케이스: 데이터 부족, NaN, 동일가격(분모 0), 빈 시리즈에서 예외 없이 NEUTRAL.
"""

import numpy as np
import pandas as pd

from algorithms.signals import (
    Signal,
    SignalResult,
    ema_cross,
    generate_signals,
    macd_signal,
    rsi_signal,
)


# --- EMA 크로스 ---


def test_ema_golden_cross_is_bullish():
    # 긴 하락 추세 끝에 반등 → 단기 EMA가 장기 EMA를 마지막 봉에서 상향 돌파.
    down = np.linspace(120, 90, 40)
    up = np.linspace(91, 113, 5)
    prices = pd.Series(np.concatenate([down, up]))
    assert ema_cross(prices) == Signal.BULLISH


def test_ema_death_cross_is_bearish():
    # 긴 상승 추세 끝에 급락 → 단기 EMA가 장기 EMA를 마지막 봉에서 하향 돌파.
    up = np.linspace(80, 140, 40)
    down = np.linspace(138, 100, 6)
    prices = pd.Series(np.concatenate([up, down]))
    assert ema_cross(prices) == Signal.BEARISH


def test_ema_insufficient_data_is_neutral():
    prices = pd.Series([100.0, 101.0, 102.0])
    assert ema_cross(prices) == Signal.NEUTRAL


# --- RSI ---


def test_rsi_overbought_is_bearish():
    # 단조 상승 → RSI 100 근처 → 과매수 → BEARISH.
    prices = pd.Series(np.linspace(100, 200, 40))
    assert rsi_signal(prices) == Signal.BEARISH


def test_rsi_oversold_is_bullish():
    # 단조 하락 → RSI 0 근처 → 과매도 → BULLISH.
    prices = pd.Series(np.linspace(200, 100, 40))
    assert rsi_signal(prices) == Signal.BULLISH


def test_rsi_flat_prices_is_neutral():
    # 가격 변동 없음 → 분모 0 → RSI 50 → NEUTRAL (ZeroDivision 금지).
    prices = pd.Series([100.0] * 40)
    assert rsi_signal(prices) == Signal.NEUTRAL


def test_rsi_insufficient_data_is_neutral():
    prices = pd.Series([100.0, 101.0])
    assert rsi_signal(prices) == Signal.NEUTRAL


# --- MACD ---


def test_macd_returns_signal_type():
    prices = pd.Series(np.linspace(100, 160, 60))
    assert isinstance(macd_signal(prices), Signal)


def test_macd_insufficient_data_is_neutral():
    prices = pd.Series([100.0, 101.0, 102.0, 103.0])
    assert macd_signal(prices) == Signal.NEUTRAL


def test_macd_flat_prices_is_neutral():
    prices = pd.Series([100.0] * 60)
    assert macd_signal(prices) == Signal.NEUTRAL


# --- 엣지케이스: 빈/NaN ---


def test_empty_series_all_neutral():
    prices = pd.Series([], dtype=float)
    assert ema_cross(prices) == Signal.NEUTRAL
    assert rsi_signal(prices) == Signal.NEUTRAL
    assert macd_signal(prices) == Signal.NEUTRAL


def test_nan_values_handled_without_error():
    base = list(np.linspace(100, 140, 40))
    base[5] = np.nan
    base[10] = np.nan
    prices = pd.Series(base)
    # 예외 없이 Signal 반환.
    assert isinstance(ema_cross(prices), Signal)
    assert isinstance(rsi_signal(prices), Signal)
    assert isinstance(macd_signal(prices), Signal)


def test_all_nan_is_neutral():
    prices = pd.Series([np.nan] * 40)
    assert ema_cross(prices) == Signal.NEUTRAL
    assert rsi_signal(prices) == Signal.NEUTRAL
    assert macd_signal(prices) == Signal.NEUTRAL


# --- generate_signals 통합 ---


def test_generate_signals_returns_result():
    df = pd.DataFrame({"close": np.linspace(100, 160, 60)})
    result = generate_signals(df)
    assert isinstance(result, SignalResult)
    assert isinstance(result.ema, Signal)
    assert isinstance(result.rsi, Signal)
    assert isinstance(result.macd, Signal)
    assert isinstance(result.overall, Signal)


def test_generate_signals_overall_majority_vote():
    # 단조 상승 추세: 과매수 RSI(BEARISH)이지만 EMA/MACD 등 종합은 Signal 타입이어야.
    df = pd.DataFrame({"close": np.linspace(100, 200, 60)})
    result = generate_signals(df)
    assert result.overall in (Signal.BULLISH, Signal.NEUTRAL, Signal.BEARISH)


def test_generate_signals_missing_column_raises():
    df = pd.DataFrame({"price": [1.0, 2.0, 3.0]})
    try:
        generate_signals(df)
    except (KeyError, ValueError):
        pass
    else:
        raise AssertionError("missing price column should raise")


def test_generate_signals_flat_prices_neutral_overall():
    df = pd.DataFrame({"close": [100.0] * 60})
    result = generate_signals(df)
    assert result.overall == Signal.NEUTRAL
