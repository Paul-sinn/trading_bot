"""CSV/Norgate-export → historical_sim 어댑터 테스트 (spec: specs/price_csv.md).

long-format 가격 데이터를 dict[symbol, OHLCV]로 로드 → historical_sim 구동. 필수 컬럼 누락/값 결측은
fail-closed. real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import HistoricalResult, run_historical_simulation
from agents.perf_report import PerformanceReport
from agents.policy_loader import load_policy
from agents.price_csv import (
    DataAdapterError,
    close_series,
    load_price_data_from_csv,
    load_price_data_from_frame,
)

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_DATES = pd.date_range("2025-01-01", periods=260, freq="B")


def _rows(symbol, close_curve, volume=1_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    vol = np.full(len(close), volume)
    vol[-1] = volume * 5
    return pd.DataFrame({
        "symbol": symbol,
        "date": _DATES,
        "open": close, "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": vol,
    })


def _long_frame():
    return pd.concat([
        _rows("NVDA", np.linspace(80, 200, 260)),
        _rows("AAPL", np.linspace(90, 180, 260)),
        _rows("SPY", np.linspace(300, 400, 260)),
        _rows("BENCH", np.linspace(100, 110, 260)),
    ], ignore_index=True)


# --- 로드 + 검증 ---


def test_load_from_frame_groups_by_symbol():
    data = load_price_data_from_frame(_long_frame())
    assert set(data) == {"NVDA", "AAPL", "SPY", "BENCH"}
    nvda = data["NVDA"]
    assert list(nvda.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(nvda.index, pd.DatetimeIndex)
    assert len(nvda) == 260


def test_load_from_csv_roundtrip(tmp_path):
    csv = tmp_path / "prices.csv"
    _long_frame().to_csv(csv, index=False)
    data = load_price_data_from_csv(csv)
    assert set(data) == {"NVDA", "AAPL", "SPY", "BENCH"}


def test_case_insensitive_columns():
    df = _long_frame().rename(columns={"close": "Close", "volume": "VOLUME", "date": "Date"})
    data = load_price_data_from_frame(df)
    assert "NVDA" in data


def test_missing_required_column_fails():
    df = _long_frame().drop(columns=["close"])
    with pytest.raises(DataAdapterError):
        load_price_data_from_frame(df)


def test_missing_file_fails():
    with pytest.raises(DataAdapterError):
        load_price_data_from_csv("does_not_exist_12345.csv")


def test_invalid_price_and_date_rows_dropped_safely():
    df = _long_frame()
    for c in ("date", "close", "volume"):  # CSV처럼 혼합 가능한 object 컬럼으로.
        df[c] = df[c].astype(object)
    df.loc[0, "close"] = np.nan          # 가격 결측
    df.loc[1, "date"] = "not-a-date"     # 날짜 무효
    df.loc[2, "volume"] = "xyz"          # 숫자 아님
    data = load_price_data_from_frame(df)   # 크래시 없이 로드
    assert "NVDA" in data
    assert len(data["NVDA"]) <= 259         # 결함 행 드롭(NVDA의 결함 행만)


def test_all_invalid_symbol_excluded():
    bad = _rows("BAD", np.full(260, np.nan))
    data = load_price_data_from_frame(pd.concat([_rows("NVDA", np.linspace(80, 200, 260)), bad], ignore_index=True))
    assert "NVDA" in data and "BAD" not in data   # 유효 행 0 → 제외


def test_close_series_helper():
    data = load_price_data_from_frame(_long_frame())
    s = close_series(data, "SPY")
    assert isinstance(s, pd.Series) and len(s) == 260
    with pytest.raises(DataAdapterError):
        close_series(data, "NOPE")


# --- historical_sim 구동 (실데이터 포맷) ---


def test_loaded_data_drives_historical_sim_with_performance():
    data = load_price_data_from_frame(_long_frame())
    spy = close_series(data, "SPY")
    bench = close_series(data, "BENCH")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    res = asyncio.run(run_historical_simulation(
        price_data={k: data[k] for k in ("NVDA", "AAPL")},  # SPY/BENCH는 컴퍼스/벤치마크로만
        spy_prices=spy, vix=vix, policy=load_policy(REAL_CONFIG),
        account_cash=1_000_000.0, benchmark_prices=bench,
        trading_days=list(spy.index[-3:]),
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    assert isinstance(res, HistoricalResult)
    assert isinstance(res.performance, PerformanceReport)
    assert len(res.performance.equity_curve) == 3
    assert res.real_orders_placed == 0


def test_loaded_data_vetoed_when_no_event_provider():
    data = load_price_data_from_frame(_long_frame())
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    res = asyncio.run(run_historical_simulation(
        price_data={k: data[k] for k in ("NVDA", "AAPL")},
        spy_prices=spy, vix=vix, policy=load_policy(REAL_CONFIG),
        account_cash=1_000_000.0, benchmark_prices=close_series(data, "BENCH"),
        trading_days=list(spy.index[-3:]),
        params=EvidenceParams(account_equity=1_000_000.0), event_provider=None,
    ))
    assert res.portfolio.positions == {}      # event 결측 → veto → 무거래
    assert res.real_orders_placed == 0
