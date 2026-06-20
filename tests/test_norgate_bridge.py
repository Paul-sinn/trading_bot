"""NDU/Norgate export → historical_sim 브리지 테스트 (spec: specs/norgate_bridge.md).

NDU 심볼별 CSV(파일명=심볼, symbol 컬럼 없음)를 로드 → historical_sim 구동. 필수 컬럼 누락 fail-closed.
real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import HistoricalResult, run_historical_simulation
from agents.norgate_bridge import (
    DataAdapterError,
    load_norgate_csv,
    load_norgate_folder,
)
from agents.perf_report import PerformanceReport
from agents.policy_loader import load_policy
from agents.price_csv import close_series

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_DATES = pd.date_range("2025-01-01", periods=260, freq="B")


def _ndu_csv(folder: Path, symbol: str, close_curve, volume=1_000_000.0):
    """NDU-style 심볼별 CSV(symbol 컬럼 없음, 대문자 컬럼) 작성."""
    close = np.asarray(close_curve, dtype=float)
    vol = np.full(len(close), volume)
    vol[-1] = volume * 5
    df = pd.DataFrame({
        "Date": _DATES.strftime("%Y-%m-%d"),
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": vol,
    })
    df.to_csv(folder / f"{symbol}.csv", index=False)


def _ndu_folder(tmp_path: Path) -> Path:
    d = tmp_path / "norgate"
    d.mkdir()
    _ndu_csv(d, "NVDA", np.linspace(80, 200, 260))
    _ndu_csv(d, "AAPL", np.linspace(90, 180, 260))
    _ndu_csv(d, "SPY", np.linspace(300, 400, 260))
    _ndu_csv(d, "BENCH", np.linspace(100, 110, 260))
    return d


# --- 로드 ---


def test_ndu_style_csv_loads_symbol_from_filename(tmp_path):
    d = tmp_path / "nd"
    d.mkdir()
    _ndu_csv(d, "NVDA", np.linspace(80, 200, 260))
    data = load_norgate_csv(d / "NVDA.csv")
    assert "NVDA" in data                          # 파일명에서 심볼 추론
    assert list(data["NVDA"].columns) == ["open", "high", "low", "close", "volume"]
    assert len(data["NVDA"]) == 260


def test_multiple_symbols_load_from_folder(tmp_path):
    data = load_norgate_folder(_ndu_folder(tmp_path))
    assert set(data) == {"NVDA", "AAPL", "SPY", "BENCH"}


def test_missing_columns_fail_safely_with_filename(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    # Close 없는 NDU 파일.
    pd.DataFrame({
        "Date": _DATES.strftime("%Y-%m-%d"),
        "Open": np.ones(260), "High": np.ones(260), "Low": np.ones(260), "Volume": np.ones(260),
    }).to_csv(d / "NVDA.csv", index=False)
    with pytest.raises(DataAdapterError) as exc:
        load_norgate_folder(d)
    assert "NVDA" in str(exc.value)                # 파일명 포함 명확한 에러


def test_missing_folder_fails(tmp_path):
    with pytest.raises(DataAdapterError):
        load_norgate_folder(tmp_path / "nope")


def test_empty_folder_fails(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(DataAdapterError):
        load_norgate_folder(d)


def test_long_format_csv_with_symbol_column(tmp_path):
    # symbol 컬럼이 있는 단일 파일도 그대로 처리(주입 안 함).
    df = pd.concat([
        pd.DataFrame({"symbol": "NVDA", "date": _DATES, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}),
        pd.DataFrame({"symbol": "AAPL", "date": _DATES, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}),
    ], ignore_index=True)
    p = tmp_path / "combined.csv"
    df.to_csv(p, index=False)
    data = load_norgate_csv(p)
    assert set(data) == {"NVDA", "AAPL"}


# --- historical_sim 구동 ---


def test_norgate_data_drives_historical_sim(tmp_path):
    data = load_norgate_folder(_ndu_folder(tmp_path))
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    res = asyncio.run(run_historical_simulation(
        price_data={k: data[k] for k in ("NVDA", "AAPL")},
        spy_prices=spy, vix=vix, policy=load_policy(REAL_CONFIG),
        account_cash=1_000_000.0, benchmark_prices=close_series(data, "BENCH"),
        trading_days=list(spy.index[-3:]),
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    assert isinstance(res, HistoricalResult)
    assert isinstance(res.performance, PerformanceReport)
    assert len(res.performance.equity_curve) == 3
    assert res.real_orders_placed == 0
