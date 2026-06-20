"""feature factory 테스트 (spec: specs/features.md).

과거 OHLCV → 재사용 피처(수익률/모멘텀/상대강도/거래량비/ATR%/고점거리/추세플래그). 순수 함수 —
매매 판단 없음. 데이터 부족은 None + missing_fields(fail-closed). real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd
import pytest

from algorithms.features import (
    FeatureError,
    FeatureSnapshot,
    compute_features,
)


def _ohlcv(close, *, volume=1_000_000.0, start="2024-01-01"):
    close = np.asarray(close, dtype=float)
    vol = np.asarray(volume, dtype=float)
    if vol.ndim == 0:
        vol = np.full(len(close), float(volume))
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=len(close), freq="B"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": vol,
        }
    ).set_index("date")


# --- 기본 계산 ---


def test_returns_computed_correctly():
    # 0.1% 일일 복리 상승 260일 → 수익률 부호 양수, 윈도우 비율 정확.
    close = 100.0 * (1.001 ** np.arange(260))
    snap = compute_features(_ohlcv(close), symbol="TEST")
    assert isinstance(snap, FeatureSnapshot)
    assert snap.symbol == "TEST"
    assert snap.return_1m == pytest.approx(close[-1] / close[-22] - 1.0)
    assert snap.return_3m == pytest.approx(close[-1] / close[-64] - 1.0)
    assert snap.return_6m == pytest.approx(close[-1] / close[-127] - 1.0)
    assert snap.return_1m > 0
    assert snap.real_orders_placed == 0
    # 벤치마크 미제공 → relative_strength만 미계산, 나머지 가격기반 피처는 전부 있음.
    assert snap.missing_fields == ("relative_strength",)


def test_momentum_score_is_weighted_blend():
    close = 100.0 * (1.001 ** np.arange(260))
    snap = compute_features(_ohlcv(close))
    expected = 0.5 * snap.return_1m + 0.3 * snap.return_3m + 0.2 * snap.return_6m
    assert snap.momentum_score == pytest.approx(expected)


def test_distance_from_high_non_positive():
    # 마지막에 하락 → 최근 고점보다 아래(음수).
    up = np.linspace(100, 200, 250)
    down = np.linspace(200, 180, 10)
    close = np.concatenate([up, down])
    snap = compute_features(_ohlcv(close))
    assert snap.distance_from_high < 0
    assert snap.distance_from_high == pytest.approx(close[-1] / close.max() - 1.0)


def test_trend_flags_uptrend():
    close = np.linspace(50, 150, 260)  # 꾸준한 상승 → 가격>20ma>50ma.
    snap = compute_features(_ohlcv(close))
    assert snap.price_above_20ma is True
    assert snap.price_above_50ma is True
    assert snap.ma20_above_ma50 is True


def test_trend_flags_downtrend():
    close = np.linspace(150, 50, 260)  # 하락 → 가격<20ma<50ma.
    snap = compute_features(_ohlcv(close))
    assert snap.price_above_20ma is False
    assert snap.ma20_above_ma50 is False


def test_volume_ratio_detects_spike():
    close = np.linspace(100, 120, 60)
    vol = np.full(60, 1_000_000.0)
    vol[-1] = 3_000_000.0  # 최근 거래량 3배.
    snap = compute_features(_ohlcv(close, volume=vol))
    assert snap.volume_ratio_20d == pytest.approx(3.0)


def test_atr_pct_reasonable():
    close = np.linspace(100, 120, 60)
    snap = compute_features(_ohlcv(close))
    # high=close*1.01, low=close*0.99 → 일중 변동 ~2% → ATR% 작은 양수.
    assert snap.atr_pct is not None
    assert 0.0 < snap.atr_pct < 0.1


# --- 상대강도(벤치마크) ---


def test_relative_strength_outperformance_positive():
    asset = 100.0 * (1.003 ** np.arange(120))   # 강함
    bench = 100.0 * (1.001 ** np.arange(120))   # 약함
    snap = compute_features(
        _ohlcv(asset), benchmark=pd.Series(bench), symbol="A"
    )
    assert snap.relative_strength is not None
    assert snap.relative_strength > 0


def test_relative_strength_underperformance_negative():
    asset = 100.0 * (1.0005 ** np.arange(120))
    bench = 100.0 * (1.002 ** np.arange(120))
    snap = compute_features(_ohlcv(asset), benchmark=pd.Series(bench))
    assert snap.relative_strength < 0


def test_relative_strength_none_without_benchmark():
    close = np.linspace(100, 120, 200)
    snap = compute_features(_ohlcv(close))
    assert snap.relative_strength is None
    assert "relative_strength" in snap.missing_fields


# --- fail-closed: 부족/결측 ---


def test_insufficient_lookback_returns_missing_fields():
    close = np.linspace(100, 110, 30)  # 6m(126) 불가, 1m(21)은 가능.
    snap = compute_features(_ohlcv(close))
    assert snap.return_6m is None
    assert "return_6m" in snap.missing_fields
    assert snap.momentum_score is None        # 6m 없으면 모멘텀도 None
    assert "momentum_score" in snap.missing_fields
    assert snap.return_1m is not None         # 1m은 계산됨
    assert snap.real_orders_placed == 0


def test_very_short_series_safe_no_exception():
    snap = compute_features(_ohlcv(np.array([100.0, 101.0, 102.0])))
    assert snap.return_1m is None
    assert snap.momentum_score is None
    assert isinstance(snap.missing_fields, tuple) and len(snap.missing_fields) > 0


def test_missing_volume_column_only_affects_volume_feature():
    close = np.linspace(100, 130, 200)
    df = _ohlcv(close).drop(columns=["volume"])
    snap = compute_features(df)
    assert snap.volume_ratio_20d is None
    assert "volume_ratio_20d" in snap.missing_fields
    assert snap.return_1m is not None          # 나머지는 정상
    assert snap.atr_pct is not None


def test_missing_high_low_only_affects_atr():
    close = np.linspace(100, 130, 200)
    df = _ohlcv(close).drop(columns=["high", "low"])
    snap = compute_features(df)
    assert snap.atr_pct is None
    assert "atr_pct" in snap.missing_fields
    assert snap.return_3m is not None


def test_empty_frame_fails_closed():
    with pytest.raises(FeatureError):
        compute_features(pd.DataFrame({"close": []}))


def test_missing_close_column_fails_closed():
    df = pd.DataFrame({"price": [1.0, 2.0, 3.0]})
    with pytest.raises(FeatureError):
        compute_features(df)


def test_nan_close_dropped_before_compute():
    close = np.linspace(100, 130, 200).astype(float)
    close[5] = np.nan
    snap = compute_features(_ohlcv(close))
    assert snap.return_1m is not None          # NaN 제거 후에도 계산
    assert snap.as_of is not None
