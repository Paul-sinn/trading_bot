"""Step 6 filters (Layer 2) 테스트 (TDD Red→Green).

spec: specs/filters.md
- 결정론적 필터(volume/atr/vix)는 순수 함수. 센티먼트는 provider 주입(Mock).
- 거래량 급등 / ATR 변동성 / 센티먼트 / VIX + apply_filters 통합(AND).
- 엣지케이스: 데이터 부족, volume 0, ATR 0(가격 불변), VIX 결측, provider 미주입.
"""

import numpy as np
import pandas as pd
import pytest

from algorithms.filters import (
    ClaudeSentimentProvider,
    FilterResult,
    MockSentimentProvider,
    apply_filters,
    atr_filter,
    sentiment_filter,
    vix_filter,
    volume_spike,
)


# --- 거래량 급등 ---


def test_volume_spike_true_on_surge():
    # 평탄한 거래량 뒤 마지막 봉 급등 → True.
    volume = pd.Series([100.0] * 19 + [1000.0])
    assert volume_spike(volume) is True


def test_volume_spike_false_when_flat():
    volume = pd.Series([100.0] * 20)
    assert volume_spike(volume) is False


def test_volume_spike_insufficient_data_is_false():
    volume = pd.Series([100.0, 200.0, 300.0])
    assert volume_spike(volume) is False


def test_volume_spike_all_zero_is_false():
    # 직전 구간 거래량 전부 0 → 이동평균 0 → 판단 불가 → False (ZeroDivision 금지).
    volume = pd.Series([0.0] * 19 + [5.0])
    assert volume_spike(volume) is False


# --- ATR 변동성 ---


def test_atr_filter_passes_low_volatility():
    # 좁은 범위로 천천히 상승 → ATR/price 작음 → 통과 True.
    n = 30
    close = np.linspace(100.0, 102.0, n)
    df = pd.DataFrame(
        {
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
        }
    )
    assert atr_filter(df) is True


def test_atr_filter_blocks_high_volatility():
    # 봉마다 큰 폭 변동 → ATR/price 큼 → 한도 초과 → False.
    n = 30
    close = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "high": close + 20.0,
            "low": close - 20.0,
            "close": close,
        }
    )
    assert atr_filter(df) is False


def test_atr_filter_flat_prices_passes():
    # 가격 불변 → ATR 0 → 비율 0 ≤ max → True. 분모 0 폭발 없음.
    n = 30
    close = np.full(n, 100.0)
    df = pd.DataFrame({"high": close, "low": close, "close": close})
    assert atr_filter(df) is True


def test_atr_filter_insufficient_data_is_false():
    df = pd.DataFrame({"high": [101.0, 102.0], "low": [99.0, 100.0], "close": [100.0, 101.0]})
    assert atr_filter(df) is False


# --- 센티먼트 (provider 주입) ---


def test_sentiment_filter_positive_with_mock():
    provider = MockSentimentProvider({"AAPL": True})
    assert sentiment_filter("AAPL", provider) is True


def test_sentiment_filter_negative_with_mock():
    provider = MockSentimentProvider({"AAPL": False})
    assert sentiment_filter("AAPL", provider) is False


def test_sentiment_filter_default_when_unknown():
    provider = MockSentimentProvider(default=False)
    assert sentiment_filter("UNKNOWN", provider) is False


def test_sentiment_filter_none_provider_raises():
    with pytest.raises(ValueError):
        sentiment_filter("AAPL", None)


def test_claude_provider_not_implemented():
    provider = ClaudeSentimentProvider(api_key="dummy")
    with pytest.raises(NotImplementedError):
        provider.is_positive("AAPL")


def test_claude_provider_no_key_raises():
    with pytest.raises((ValueError, NotImplementedError)):
        ClaudeSentimentProvider(api_key=None).is_positive("AAPL")


# --- VIX ---


def test_vix_filter_below_limit_passes():
    assert vix_filter(20.0) is True


def test_vix_filter_at_limit_passes():
    assert vix_filter(30.0) is True


def test_vix_filter_above_limit_blocks():
    assert vix_filter(35.0) is False


def test_vix_filter_none_is_false():
    assert vix_filter(None) is False
    assert vix_filter(float("nan")) is False


# --- apply_filters 통합 (AND) ---


def _good_df(n: int = 30) -> pd.DataFrame:
    close = np.linspace(100.0, 102.0, n)
    volume = np.full(n, 100.0)
    volume[-1] = 1000.0  # 마지막 봉 거래량 급등
    return pd.DataFrame(
        {
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": volume,
        }
    )


def test_apply_filters_all_pass():
    df = _good_df()
    provider = MockSentimentProvider(default=True)
    result = apply_filters(df, "AAPL", vix=20.0, sentiment_provider=provider)
    assert isinstance(result, FilterResult)
    assert result.volume is True
    assert result.atr is True
    assert result.sentiment is True
    assert result.vix is True
    assert result.passed is True


def test_apply_filters_fails_if_one_fails():
    # VIX만 한도 초과 → 전체 fail.
    df = _good_df()
    provider = MockSentimentProvider(default=True)
    result = apply_filters(df, "AAPL", vix=50.0, sentiment_provider=provider)
    assert result.vix is False
    assert result.passed is False


def test_apply_filters_fails_on_negative_sentiment():
    df = _good_df()
    provider = MockSentimentProvider(default=False)
    result = apply_filters(df, "AAPL", vix=20.0, sentiment_provider=provider)
    assert result.sentiment is False
    assert result.passed is False


def test_apply_filters_missing_column_raises():
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
    provider = MockSentimentProvider(default=True)
    with pytest.raises(KeyError):
        apply_filters(df, "AAPL", vix=20.0, sentiment_provider=provider)
