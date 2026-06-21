"""entry_fill_mode 테스트 (spec: specs/entry_fill_mode.md).

opt-in 다음-바 진입 체결. 기본(current) 불변. 미체결은 트레이드/포지션 안 만듦. 다음 바 결측 안전.
real_orders=0. 네트워크 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import resolve_entry_fill, run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


# --- 순수 체결 결정 ---


def test_resolve_current_unchanged():
    assert resolve_entry_fill(100.0, (101.0, 102.0, 100.5), "current", 0.03) == 100.0
    assert resolve_entry_fill(100.0, None, "current", 0.03) == 100.0   # current는 바 무관


def test_resolve_next_bar_limit_fills_at_open():
    # next_open 98 <= limit 103 → open 체결.
    assert resolve_entry_fill(100.0, (98.0, 99.0, 97.0), "next-bar-limit", 0.03) == 98.0


def test_resolve_next_bar_limit_fills_at_limit():
    # next_open 105 > limit 103, next_low 102 <= 103 → limit 체결.
    assert resolve_entry_fill(100.0, (105.0, 106.0, 102.0), "next-bar-limit", 0.03) == 103.0


def test_resolve_next_bar_limit_miss_on_gap_up():
    # 갭업 — next_low 104 > limit 103 → 미체결.
    assert resolve_entry_fill(100.0, (105.0, 106.0, 104.0), "next-bar-limit", 0.03) is None


def test_resolve_next_open():
    assert resolve_entry_fill(100.0, (107.0, 108.0, 106.0), "next-open", 0.03) == 107.0


def test_resolve_missing_next_bar_is_none():
    assert resolve_entry_fill(100.0, None, "next-bar-limit", 0.03) is None
    assert resolve_entry_fill(100.0, None, "next-open", 0.03) is None


# --- 통합: historical_sim ---


def _rows(symbol, close, volume):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.asarray(volume, dtype=float),
    })


def _setup():
    n = 260
    vol = np.full(n, 1_000_000.0)
    vol[200:] = 6_000_000.0   # 거래량 급등(후보 생성) — 중간~끝.
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, n), vol),
        _rows("SPY", np.linspace(300, 400, n), np.full(n, 1e6)),
        _rows("BENCH", np.linspace(100, 110, n), np.full(n, 1e6)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    return data, spy, vix


def _run(days, *, model="current", buffer=0.03):
    data, spy, vix = _setup()
    return asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
        entry_fill_model=model, entry_limit_buffer_pct=buffer,
    )), spy


def test_default_current_unchanged():
    _, spy = _setup(), None
    data, spy, vix = _setup()
    days = list(spy.index[203:206])
    base = asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    cur = asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
        entry_fill_model="current",
    ))
    assert base.trade_log == cur.trade_log          # 기본/current 동일
    assert base.real_orders_placed == 0


def test_next_open_fills_when_next_bar_exists():
    data, spy, vix = _setup()
    days = [spy.index[205]]      # 다음 바 206 존재.
    res, _ = _run(days, model="next-open")
    assert "NVDA" in res.portfolio.positions      # next_open 체결 → 포지션 생성
    assert res.real_orders_placed == 0


def test_next_bar_limit_wide_buffer_fills():
    data, spy, vix = _setup()
    days = [spy.index[205]]
    res, _ = _run(days, model="next-bar-limit", buffer=0.50)   # 넓은 버퍼 → 체결
    assert "NVDA" in res.portfolio.positions
    assert res.real_orders_placed == 0


def _setup_tail_spike():
    # 거래량 급등을 마지막 3바에만 → 마지막 바(259)에서도 후보 생성(baseline은 낮게 유지).
    n = 260
    vol = np.full(n, 1_000_000.0)
    vol[257:] = 6_000_000.0
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, n), vol),
        _rows("SPY", np.linspace(300, 400, n), np.full(n, 1e6)),
        _rows("BENCH", np.linspace(100, 110, n), np.full(n, 1e6)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    return data, spy, vix


def _run_data(data, spy, vix, days, *, model):
    return asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
        entry_fill_model=model,
    ))


def test_missing_next_bar_creates_no_trade():
    data, spy, vix = _setup_tail_spike()
    days = [spy.index[259]]      # 마지막 바 — 다음 바 없음.
    res_cur = _run_data(data, spy, vix, days, model="current")
    res_real = _run_data(data, spy, vix, days, model="next-open")
    assert "NVDA" in res_cur.portfolio.positions          # current는 체결
    assert res_real.portfolio.positions == {}             # 다음 바 결측 → 미체결, 포지션 없음
    assert res_real.portfolio.trade_log == ()             # 트레이드/체결 없음
    assert res_real.real_orders_placed == 0


def test_real_orders_zero_all_models():
    data, spy, vix = _setup()
    days = [spy.index[205]]
    for model in ("current", "next-bar-limit", "next-open"):
        res, _ = _run(days, model=model)
        assert res.real_orders_placed == 0
