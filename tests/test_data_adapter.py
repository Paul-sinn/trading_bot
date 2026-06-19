"""Phase 5 step6 — 일봉 OHLCV 데이터 어댑터 테스트 (TDD Red→Green).

spec: specs/data_adapter.md  ·  헌장: §3/§10
- 네트워크 호출 절대 금지: MockDailyProvider / fetch_fn 주입으로 검증.
- 정규화 형식이 백테스트 엔진(step5) 기대와 일치(컬럼·dtype·정렬).
- I/O는 agents/(ADR-001). 생존편향 경고 명기.
"""

import numpy as np
import pandas as pd

from agents.data_adapter import (
    SURVIVORSHIP_WARNING,
    DailyDataProvider,
    FreeDailyProvider,
    MockDailyProvider,
    normalize_ohlcv,
)


def _raw_yf_style(n: int = 10) -> pd.DataFrame:
    # yfinance 스타일(대문자 컬럼 + Adj Close), 역순 인덱스로 정렬 검증.
    idx = pd.date_range("2024-01-01", periods=n, freq="D")[::-1]
    base = np.linspace(100, 110, n)
    return pd.DataFrame(
        {
            "Open": base,
            "High": base * 1.01,
            "Low": base * 0.99,
            "Close": base,
            "Adj Close": base * 0.95,
            "Volume": np.full(n, 1000),
        },
        index=idx,
    )


# --- normalize_ohlcv ---


def test_normalize_lowercases_and_selects_columns():
    out = normalize_ohlcv(_raw_yf_style())
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_all_float64():
    out = normalize_ohlcv(_raw_yf_style())
    for col in out.columns:
        assert out[col].dtype == np.float64


def test_normalize_sorts_index_ascending():
    out = normalize_ohlcv(_raw_yf_style())
    assert out.index.is_monotonic_increasing


def test_normalize_prefers_adjusted_close():
    raw = _raw_yf_style()
    out = normalize_ohlcv(raw)
    # Adj Close(=Close*0.95)가 close로 채택돼야 한다.
    assert out["close"].iloc[0] < raw["Close"].min() * 0.99 + 1e-9 or (
        abs(out["close"].sort_index().iloc[0] - raw["Adj Close"].sort_index().iloc[0]) < 1e-6
    )


def test_normalize_drops_nan_rows():
    raw = _raw_yf_style()
    raw.iloc[3, raw.columns.get_loc("Close")] = np.nan
    out = normalize_ohlcv(raw)
    assert not out["close"].isna().any()


def test_normalize_flattens_yfinance_multiindex_columns():
    # yfinance는 단일 심볼도 MultiIndex 컬럼(('Close','AAPL') 등)으로 반환할 수 있다.
    raw = _raw_yf_style()
    raw.columns = pd.MultiIndex.from_product([list(raw.columns), ["AAPL"]])
    out = normalize_ohlcv(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_missing_column_raises():
    raw = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0]})  # close/volume 없음
    try:
        normalize_ohlcv(raw)
    except KeyError:
        pass
    else:
        raise AssertionError("missing OHLCV column should raise KeyError")


# --- MockDailyProvider ---


def _clean_df(n: int = 10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    base = np.linspace(100, 110, n)
    return pd.DataFrame(
        {
            "open": base, "high": base * 1.01, "low": base * 0.99,
            "close": base, "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_mock_provider_conforms_to_protocol():
    provider = MockDailyProvider({"AAA": _clean_df()}, vix=pd.Series([15.0] * 10))
    assert isinstance(provider, DailyDataProvider)


def test_mock_provider_returns_normalized_ohlcv():
    provider = MockDailyProvider({"AAA": _clean_df()}, vix=pd.Series([15.0] * 10))
    out = provider.get_ohlcv("AAA")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_mock_provider_unknown_symbol_raises():
    provider = MockDailyProvider({"AAA": _clean_df()}, vix=pd.Series([15.0] * 10))
    try:
        provider.get_ohlcv("ZZZ")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown symbol should raise KeyError")


def test_mock_provider_get_vix_returns_series():
    provider = MockDailyProvider({"AAA": _clean_df()}, vix=pd.Series([15.0] * 10))
    vix = provider.get_vix()
    assert isinstance(vix, pd.Series)


# --- FreeDailyProvider (fetch_fn 주입 — 네트워크 없음) ---


def test_free_provider_uses_injected_fetch_no_network():
    calls = []

    def fake_fetch(symbol, start, end):
        calls.append(symbol)
        return _raw_yf_style()

    provider = FreeDailyProvider(fetch_fn=fake_fetch)
    out = provider.get_ohlcv("AAA", "2024-01-01", "2024-01-10")
    assert calls == ["AAA"]
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.is_monotonic_increasing


def test_free_provider_survivorship_flag():
    provider = FreeDailyProvider(fetch_fn=lambda s, a, b: _raw_yf_style())
    assert provider.survivorship_biased is True
    assert isinstance(SURVIVORSHIP_WARNING, str) and SURVIVORSHIP_WARNING


def test_free_provider_conforms_to_protocol():
    provider = FreeDailyProvider(fetch_fn=lambda s, a, b: _raw_yf_style())
    assert isinstance(provider, DailyDataProvider)


# --- 백테스트 엔진과의 형식 호환 ---


def test_adapter_output_feeds_backtest_engine():
    from algorithms.backtest import run_backtest

    provider = MockDailyProvider(
        {"AAA": _clean_df(260)}, vix=pd.Series([15.0] * 260)
    )
    price_data = {"AAA": provider.get_ohlcv("AAA")}
    spy = provider.get_ohlcv("AAA")  # 형식 호환만 확인(동일 형식)
    vix = provider.get_vix()
    res = run_backtest(price_data, spy, vix)
    assert res.total_trades >= 0  # 예외 없이 동작


# --- step11: NorgateProvider (실연동 — fake SDK 주입, 네트워크/실 NDU 0) ---
#
# CRITICAL: 실제 norgatedata/NDU 호출 금지. provider._sdk()를 fake로 갈아끼워 매핑만 검증한다.

import sys
import types

from agents.data_adapter import NorgateProvider, PointInTimeProvider
from algorithms.universe import SymbolMetrics


def _norgate_style_hist(
    dates: pd.DatetimeIndex, close: float = 100.0, half_range: float = 1.0,
    turnover: float = 1.0e8,
) -> pd.DataFrame:
    """Norgate price_timeseries 스타일 df(상단 대문자 + Turnover, index=Date)."""
    n = len(dates)
    c = pd.Series([close] * n, index=dates, dtype="float64")
    df = pd.DataFrame(
        {
            "Open": c, "High": c + half_range, "Low": c - half_range, "Close": c,
            "Volume": pd.Series([1.0e6] * n, index=dates),
            "Turnover": pd.Series([turnover] * n, index=dates),
            "Unadjusted Close": c, "Dividend": pd.Series([0.0] * n, index=dates),
        }
    )
    df.index.name = "Date"
    return df


class _FakeNorgate:
    """norgatedata 패키지의 fake (네트워크 없음). 실측 시그니처와 동일한 호출만 받는다."""

    StockPriceAdjustmentType = types.SimpleNamespace(
        TOTALRETURN="TR", CAPITAL="CAP", CAPITALSPECIAL="CAPS", NONE="NONE"
    )

    def __init__(self):
        biz = pd.bdate_range("2022-06-01", "2024-01-31")
        dead_biz = pd.bdate_range("2022-06-01", "2023-06-01")
        self._hist = {
            "GOOD": _norgate_style_hist(biz),
            "LEV3X": _norgate_style_hist(biz),
            "DEAD": _norgate_style_hist(dead_biz),
            "SPY": _norgate_style_hist(biz),
            "$VIX": _norgate_style_hist(biz, close=16.0, turnover=0.0),
        }
        self._names = {
            "GOOD": "Good Industries Common",
            "LEV3X": "Direxion Daily Semiconductor Bull 3X Shares",
            "DEAD": "Defunct Corp Common",
            "SPY": "SPDR S&P 500 ETF",
            "$VIX": "CBOE Volatility Index",
        }
        self._delisted = {"DEAD": "2023-06-01"}
        self.calls: list[str] = []

    def price_timeseries(self, symbol, stock_price_adjustment_setting=None,
                         start_date=None, end_date=None, format=None, **kw):
        self.calls.append(f"price:{symbol}")
        df = self._hist[symbol].copy()
        if start_date:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df

    def watchlist_symbols(self, watchlistname: str):
        self.calls.append(f"wl:{watchlistname}")
        return ["GOOD", "LEV3X", "DEAD"]

    def first_quoted_date(self, symbol, **kw):
        return "2022-06-01"

    def last_quoted_date(self, symbol, **kw):
        return self._delisted.get(symbol)  # active면 None

    def security_name(self, symbol):
        return self._names.get(symbol, "")


def _provider_with_fake():
    fake = _FakeNorgate()
    provider = NorgateProvider(universe_watchlist="S&P 500 Current & Past")
    provider._sdk = lambda: fake  # 인스턴스 속성이 메서드를 가린다 → 실 import 안 함
    return provider, fake


def test_norgate_survivorship_flag_false():
    provider, _ = _provider_with_fake()
    assert provider.survivorship_biased is False  # 상폐 포함 → 편향 없음


def test_norgate_conforms_to_protocol():
    provider, _ = _provider_with_fake()
    assert isinstance(provider, PointInTimeProvider)


def test_norgate_get_ohlcv_normalizes():
    provider, _ = _provider_with_fake()
    out = provider.get_ohlcv("GOOD", "2023-01-01", "2023-12-31")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index.is_monotonic_increasing
    assert out.index.min() >= pd.Timestamp("2023-01-01")
    assert out.index.max() <= pd.Timestamp("2023-12-31")


def test_norgate_get_vix_returns_close_series():
    provider, _ = _provider_with_fake()
    vix = provider.get_vix("2023-01-01", "2023-06-30")
    assert isinstance(vix, pd.Series)
    assert (vix == 16.0).all()  # fake VIX close = 16


def test_norgate_get_metrics_maps_fields():
    provider, _ = _provider_with_fake()
    metrics = provider.get_metrics("2023-01-01")
    assert set(metrics) == {"GOOD", "LEV3X", "DEAD"}
    good = metrics["GOOD"]
    assert isinstance(good, SymbolMetrics)
    assert good.listed_from == "2022-06-01"
    assert good.delisted_at is None  # active
    assert good.avg_dollar_volume > 1e7  # Turnover 1e8
    assert 0.015 <= good.atr_pct <= 0.05  # half_range=1, close=100 → ATR%≈0.02
    assert good.is_leveraged_or_inverse is False
    assert metrics["LEV3X"].is_leveraged_or_inverse is True  # 'Bull 3X'
    assert metrics["DEAD"].delisted_at == "2023-06-01"


def test_norgate_get_constituents_pointintime():
    provider, _ = _provider_with_fake()
    # 상폐(2023-06-01) 이전: DEAD 포함, LEV3X(레버리지) 제외
    before = provider.get_constituents("2023-01-01")
    assert before == ["DEAD", "GOOD"]
    # 상폐 이후: DEAD 제외 (미래참조/생존편향 양방향 금지)
    after = provider.get_constituents("2023-09-01")
    assert "DEAD" not in after
    assert "GOOD" in after


def test_norgate_no_real_network_lazy_import_message():
    # norgatedata 미설치 시뮬레이션: sys.modules에 None 주입 → import 실패 → 안내 ImportError.
    provider = NorgateProvider()
    saved = sys.modules.get("norgatedata", "absent")
    sys.modules["norgatedata"] = None  # type: ignore[assignment]
    try:
        try:
            provider.get_ohlcv("AAPL")
        except ImportError as e:
            assert "norgatedata" in str(e)
        else:
            raise AssertionError("미설치 시 ImportError(안내) 떠야 함")
    finally:
        if saved == "absent":
            del sys.modules["norgatedata"]
        else:
            sys.modules["norgatedata"] = saved  # type: ignore[assignment]
