"""백테스트 데이터 → 멀티데이 시뮬 통합 테스트 (spec: specs/historical_sim.md).

과거 일봉을 일별 point-in-time 슬라이스로 먹여 전 구간 구동 → 성과 리포트. 포트폴리오 이월, vetoed
무거래, 데이터 결측 안전, real orders=0. 전략 미변경. 네트워크/브로커 없음.
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

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_IDX = pd.date_range("2025-01-01", periods=260, freq="B")


def _ohlcv(close_curve, volume=1_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    vol = np.full(len(close), volume)
    vol[-1] = volume * 5
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": vol},
        index=_IDX,
    )


def _data():
    return {
        "NVDA": _ohlcv(np.linspace(80, 200, 260)),
        "AAPL": _ohlcv(np.linspace(90, 180, 260)),
    }


_SPY = pd.Series(np.linspace(300, 400, 260), index=_IDX)
_VIX = pd.Series(np.full(260, 15.0), index=_IDX)
_BENCH = pd.Series(np.linspace(100, 110, 260), index=_IDX)
_DAYS = list(_IDX[-3:])  # 마지막 3거래일


def _run(*, event_provider, account_cash=1_000_000.0, trading_days=_DAYS):
    return asyncio.run(run_historical_simulation(
        price_data=_data(), spy_prices=_SPY, vix=_VIX, policy=load_policy(REAL_CONFIG),
        account_cash=account_cash, benchmark_prices=_BENCH, trading_days=trading_days,
        params=EvidenceParams(account_equity=1_000_000.0), event_provider=event_provider,
    ))


# --- 엔드투엔드 ---


def test_historical_loop_runs_end_to_end():
    res = _run(event_provider=MockEventRiskProvider(default=True))
    assert isinstance(res, HistoricalResult)
    assert len(res.multiday.day_results) == 3
    assert isinstance(res.performance, PerformanceReport)
    assert res.real_orders_placed == 0


def test_performance_report_produced():
    res = _run(event_provider=MockEventRiskProvider(default=True))
    perf = res.performance
    assert len(perf.equity_curve) == 3                 # 일별 equity
    assert len(perf.exposure_over_time) == 3
    assert perf.real_orders_placed == 0


def test_portfolio_persists_across_historical_days():
    # 첫날 매수 후 포지션이 이후 날에도 유지(이월). 거래 발생 확인.
    res = _run(event_provider=MockEventRiskProvider(default=True))
    pf = res.portfolio
    assert len(pf.positions) >= 1                      # 보유 포지션 존재
    assert len(res.trade_log) >= 1
    # 일별 스냅샷이 누적(첫날 ≤ 마지막날 거래 수).
    snaps = res.multiday.daily_snapshots
    assert snaps[0].trade_count <= snaps[-1].trade_count
    assert res.real_orders_placed == 0


def test_vetoed_candidates_create_no_trades():
    # event_provider 없음 → 전 후보 event_risk veto → 거래 없음.
    res = _run(event_provider=None)
    assert res.portfolio.positions == {}
    assert res.trade_log == ()
    assert res.real_orders_placed == 0


def test_missing_symbol_data_is_safe():
    # 데이터에 없는 심볼이 trading_days에 영향 없음 — 그냥 후보 없음. (이력 부족 심볼 추가)
    data = _data()
    data["NEW"] = _ohlcv(np.linspace(10, 12, 260)).iloc[-5:]  # 5봉만 → 후보 불가
    res = asyncio.run(run_historical_simulation(
        price_data=data, spy_prices=_SPY, vix=_VIX, policy=load_policy(REAL_CONFIG),
        account_cash=1_000_000.0, benchmark_prices=_BENCH, trading_days=_DAYS,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    assert "NEW" not in res.portfolio.positions        # 이력 부족 → 거래 안 됨
    assert res.real_orders_placed == 0


def test_empty_trading_days():
    res = _run(event_provider=MockEventRiskProvider(default=True), trading_days=[])
    assert res.multiday.day_results == ()
    assert res.portfolio.cash == 1_000_000.0
    assert res.real_orders_placed == 0
