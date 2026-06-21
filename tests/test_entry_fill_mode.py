"""entry_fill_mode 테스트 (spec: specs/entry_fill_mode.md, specs/entry_fill_mode 체결일 정렬).

opt-in 다음-바 진입 체결 + 체결일 정렬(포지션/트레이드가 fill_date에 시작). 기본(current) 불변.
미체결은 트레이드/포지션 안 만듦. exits/mark-to-market는 fill_date 기준. real_orders=0. 네트워크 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import resolve_entry_fill, run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from agents.sim_exit import ExitPolicy
from agents.trade_diagnostics import compute_trade_diagnostics

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


# --- 순수 체결 결정 ---


def test_resolve_current_unchanged():
    assert resolve_entry_fill(100.0, (101.0, 102.0, 100.5), "current", 0.03) == 100.0
    assert resolve_entry_fill(100.0, None, "current", 0.03) == 100.0


def test_resolve_next_bar_limit_fills_at_open():
    assert resolve_entry_fill(100.0, (98.0, 99.0, 97.0), "next-bar-limit", 0.03) == 98.0


def test_resolve_next_bar_limit_fills_at_limit():
    assert resolve_entry_fill(100.0, (105.0, 106.0, 102.0), "next-bar-limit", 0.03) == 103.0


def test_resolve_next_bar_limit_miss_on_gap_up():
    assert resolve_entry_fill(100.0, (105.0, 106.0, 104.0), "next-bar-limit", 0.03) is None


def test_resolve_next_open():
    assert resolve_entry_fill(100.0, (107.0, 108.0, 106.0), "next-open", 0.03) == 107.0


def test_resolve_missing_next_bar_is_none():
    assert resolve_entry_fill(100.0, None, "next-bar-limit", 0.03) is None
    assert resolve_entry_fill(100.0, None, "next-open", 0.03) is None


# --- 통합 fixtures ---


def _rows(symbol, close, volume):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.asarray(volume, dtype=float),
    })


def _setup_spike_at(indices):
    n = 260
    vol = np.full(n, 1_000_000.0)
    for ix in indices:
        vol[ix] = 6_000_000.0
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, n), vol),
        _rows("SPY", np.linspace(300, 400, n), np.full(n, 1e6)),
        _rows("BENCH", np.linspace(100, 110, n), np.full(n, 1e6)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    return data, spy, vix


def _run(data, spy, vix, days, *, model="current", buffer=0.03, exit_policy=None):
    return asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
        entry_fill_model=model, entry_limit_buffer_pct=buffer, exit_policy=exit_policy,
    ))


def _entry_dates(res):
    return {t.entry_date for t in compute_trade_diagnostics(res.multiday).trades}


# --- 기본 불변 ---


def test_default_current_unchanged():
    data, spy, vix = _setup_spike_at(range(200, 260))
    days = list(spy.index[203:206])
    base = asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    cur = _run(data, spy, vix, days, model="current")
    assert base.trade_log == cur.trade_log
    assert base.real_orders_placed == 0


# --- 체결일 정렬 ---


def test_next_open_uses_next_bar_as_entry_date():
    data, spy, vix = _setup_spike_at([205, 206])
    days = list(spy.index[205:207])    # 205, 206
    d205, d206 = str(spy.index[205].date()), str(spy.index[206].date())
    cur = _run(data, spy, vix, days, model="current")
    nxt = _run(data, spy, vix, days, model="next-open")
    assert "NVDA" in nxt.portfolio.positions          # 205 신호 → 206 체결
    assert d205 in _entry_dates(cur)                  # current는 신호일 체결
    assert _entry_dates(nxt) == {d206}                # next-open은 체결일(206)만
    assert d205 not in _entry_dates(nxt)
    assert nxt.real_orders_placed == 0


def test_next_bar_limit_wide_buffer_fills_on_next_bar():
    data, spy, vix = _setup_spike_at([205, 206])
    days = list(spy.index[205:207])
    d206 = str(spy.index[206].date())
    res = _run(data, spy, vix, days, model="next-bar-limit", buffer=0.50)
    assert "NVDA" in res.portfolio.positions
    assert _entry_dates(res) == {d206}
    assert res.real_orders_placed == 0


def test_mark_to_market_not_before_fill_date():
    data, spy, vix = _setup_spike_at([205, 206])
    days = list(spy.index[205:207])
    nxt = _run(data, spy, vix, days, model="next-open")
    snap0 = nxt.multiday.day_results[0].report.portfolio_snapshot   # 신호일 205
    snap1 = nxt.multiday.day_results[1].report.portfolio_snapshot   # 체결일 206
    assert snap0.total_exposure == 0.0                # 체결 전 — 포지션 없음
    assert snap1.total_exposure > 0.0                 # 체결일에 포지션 등장


def test_exits_time_stop_counts_from_fill_date():
    data, spy, vix = _setup_spike_at([205, 206])
    days = list(spy.index[205:210])
    d206, d208 = str(spy.index[206].date()), str(spy.index[208].date())
    policy_exit = ExitPolicy(stop_loss_pct=None, trail_pct=None, max_hold_days=2, manual_exit_date=None)
    nxt = _run(data, spy, vix, days, model="next-open", exit_policy=policy_exit)
    diag = compute_trade_diagnostics(nxt.multiday)
    leg = next(t for t in diag.trades if t.entry_date == d206)
    assert leg.exit_reason == "time_stop"
    assert leg.exit_date == d208                      # 체결일 206 + 2바 → 208 (신호 205 기준 아님)


def test_missing_next_bar_creates_no_trade():
    data, spy, vix = _setup_spike_at([259])           # 마지막 바에 신호, 다음 바 없음
    days = [spy.index[259]]
    res_cur = _run(data, spy, vix, days, model="current")
    res_real = _run(data, spy, vix, days, model="next-open")
    assert "NVDA" in res_cur.portfolio.positions      # current는 체결
    assert res_real.portfolio.positions == {}         # 다음 바 결측 → 미체결
    assert res_real.portfolio.trade_log == ()
    assert res_real.real_orders_placed == 0


def test_real_orders_zero_all_models():
    data, spy, vix = _setup_spike_at([205, 206])
    days = list(spy.index[205:207])
    for model in ("current", "next-bar-limit", "next-open"):
        res = _run(data, spy, vix, days, model=model)
        assert res.real_orders_placed == 0
