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
