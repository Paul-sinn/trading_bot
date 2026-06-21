"""레버리지 ETF 주말청산 테스트 (spec: specs/trend_leverage_experiment.md).

ExitPolicy.weekend_exit_symbols(레버리지 전용) + DayInput.pre_weekend. 일반주 미적용, 기본 불변.
real_orders=0. 네트워크 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import _pre_weekend_flags, run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from agents.sim_exit import (
    ExitParams,
    ExitPolicy,
    ExitReason,
    evaluate_exit,
    exit_params_for_position,
)
from agents.trade_diagnostics import compute_trade_diagnostics

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


# --- 순수 ---


def test_evaluate_exit_weekend_reason():
    d = evaluate_exit(price=100.0, shares_held=10, params=ExitParams(weekend_exit=True))
    assert d.should_exit is True
    assert d.reason == ExitReason.WEEKEND_EXIT


def test_exit_params_weekend_flag():
    p = exit_params_for_position(ExitPolicy(weekend_exit_symbols=frozenset({"TQQQ"})),
                                 avg_entry_price=100.0, hold_days=1, weekend=True)
    assert p.weekend_exit is True


def test_exit_policy_active_with_weekend_symbols():
    assert ExitPolicy(weekend_exit_symbols=frozenset({"TQQQ"})).is_active is True
    assert ExitPolicy().is_active is False        # 기본 비활성 불변


def test_pre_weekend_flags_marks_fridays():
    # 2024-01-01은 월요일 → freq=B로 index4=금요일.
    days = list(pd.date_range("2024-01-01", periods=10, freq="B"))
    flags = _pre_weekend_flags(days)
    assert flags[4] is True       # 금요일(다음 거래일 월요일 — 주말 사이)
    assert flags[0] is False      # 월요일
    assert flags[3] is False      # 목요일


# --- 통합 ---


def _rows(symbol, close, volume):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.asarray(volume, dtype=float),
    })


def _setup(spike_idx):
    n = 260
    vol = np.full(n, 1_000_000.0)
    vol[spike_idx] = 6_000_000.0
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, n), vol),
        _rows("SPY", np.linspace(300, 400, n), np.full(n, 1e6)),
        _rows("BENCH", np.linspace(100, 110, n), np.full(n, 1e6)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    return data, spy, vix


def _run(days, weekend_symbols):
    data, spy, vix = _setup(205)
    return asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
        exit_policy=ExitPolicy(weekend_exit_symbols=frozenset(weekend_symbols)),
    )), spy


def test_weekend_exit_targets_leveraged_symbol():
    # 진입 205(월), 금요일은 index209. NVDA를 weekend 대상으로 → 금요일 강제청산.
    data, spy, vix = _setup(205)
    days = list(spy.index[205:210])    # 205~209 (209=금요일)
    res, _ = _run(days, {"NVDA"})
    legs = compute_trade_diagnostics(res.multiday).trades
    weekend_legs = [t for t in legs if t.exit_reason == "weekend_exit"]
    assert len(weekend_legs) >= 1
    assert weekend_legs[0].symbol == "NVDA"
    assert "NVDA" not in res.portfolio.positions     # 청산됨
    assert res.real_orders_placed == 0


def test_weekend_exit_skips_normal_stock():
    # NVDA가 weekend 대상이 아니면(다른 심볼 지정) 금요일에도 청산 안 됨 → 보유 유지.
    data, spy, vix = _setup(205)
    days = list(spy.index[205:210])
    res, _ = _run(days, {"OTHER"})
    legs = compute_trade_diagnostics(res.multiday).trades
    assert not any(t.exit_reason == "weekend_exit" for t in legs)
    assert "NVDA" in res.portfolio.positions          # 일반주 — 주말청산 미적용, OPEN 유지
    assert res.real_orders_placed == 0
