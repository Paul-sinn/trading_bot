"""다일 시뮬레이션 루프 테스트 (spec: specs/multiday.md).

같은 시뮬 포트폴리오를 날 넘겨 이월: Day2가 Day1 포지션/현금을 봄, 매매로그 누적, vetoed Day2 불변,
일별 스냅샷, real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd

from agents.base import AgentRegistry
from agents.multiday import DayInput, MultiDayResult, run_phase1_multiday
from agents.phase1_flow import CandidateContext
from agents.policy_loader import load_policy
from agents.scanner import MockPriceDataProvider, ScannerAgent
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _bullish_df():
    prices = np.linspace(80, 200, 260)
    vol = np.full(len(prices), 1_000_000.0)
    vol[-1] = 5_000_000.0
    return pd.DataFrame(
        {"open": prices, "high": prices * 1.005, "low": prices * 0.995,
         "close": prices, "volume": vol}
    )


def _scanner(symbols):
    return ScannerAgent(
        AgentRegistry(), MockPriceDataProvider({s: _bullish_df() for s in symbols}), list(symbols)
    )


def _ctx(quantity=10, reference_price=100.0, **ov) -> CandidateContext:
    base = dict(
        stop_loss_pct=0.05, per_trade_risk_pct=0.04, regime=Regime.NORMAL_BULL,
        quantity=quantity, reference_price=reference_price,
        trend_confirmed=True, volume_confirmed=True, relative_strength_confirmed=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return CandidateContext(**base)


def _day(date, symbols, contexts):
    return DayInput(date=date, scanner=_scanner(symbols), contexts=contexts)


def _run(days, *, account_cash):
    policy = load_policy(REAL_CONFIG)
    return asyncio.run(run_phase1_multiday(days=days, policy=policy, account_cash=account_cash))


def test_day2_sees_day1_positions_and_cash():
    res = _run(
        [
            _day("2026-06-19", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("2026-06-22", ["AAPL"], {"AAPL": _ctx(5, 200.0)}),
        ],
        account_cash=100_000.0,
    )
    assert isinstance(res, MultiDayResult)
    pf = res.portfolio
    # Day1 NVDA 포지션이 Day2에도 살아있고, AAPL이 추가됨.
    assert set(pf.positions) == {"NVDA", "AAPL"}
    # Day2 시작 현금 = Day1 종료 현금(이월). Day2 스냅샷 현금 < Day1 스냅샷 현금.
    snaps = res.daily_snapshots
    assert snaps[1].cash < snaps[0].cash
    assert snaps[0].open_positions == 1 and snaps[1].open_positions == 2
    assert res.real_orders_placed == 0


def test_day2_blocked_by_day1_reduced_cash():
    # 시작 1500: Day1 NVDA(≈1001) 체결 → Day2는 줄어든 현금(~499)으로 AAPL(≈1001) 차단.
    res = _run(
        [
            _day("d1", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("d2", ["AAPL"], {"AAPL": _ctx(10, 100.0)}),
        ],
        account_cash=1500.0,
    )
    pf = res.portfolio
    assert set(pf.positions) == {"NVDA"}                  # Day2 차단
    assert len(res.day_results[1].simulated_orders) == 0
    assert res.real_orders_placed == 0


def test_multiday_trade_log_accumulates():
    res = _run(
        [
            _day("d1", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("d2", ["AAPL"], {"AAPL": _ctx(5, 200.0)}),
        ],
        account_cash=100_000.0,
    )
    assert len(res.trade_log) == 2                        # 누적
    assert {t.symbol for t in res.trade_log} == {"NVDA", "AAPL"}


def test_vetoed_day2_candidate_does_not_change_state():
    res = _run(
        [
            _day("d1", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("d2", ["AAPL"], {"AAPL": _ctx(10, 100.0, data_ok=False)}),  # veto
        ],
        account_cash=100_000.0,
    )
    pf = res.portfolio
    assert set(pf.positions) == {"NVDA"}                  # Day2 vetoed → 불변
    assert len(res.trade_log) == 1
    assert res.day_results[1].report.riskgate_vetoes == 1
    assert res.real_orders_placed == 0


def test_final_result_includes_per_day_snapshots():
    res = _run(
        [
            _day("d1", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("d2", ["AAPL"], {"AAPL": _ctx(5, 200.0)}),
        ],
        account_cash=100_000.0,
    )
    snaps = res.daily_snapshots
    assert len(snaps) == 2
    assert all(s is not None for s in snaps)
    assert snaps[0].trade_count == 1 and snaps[1].trade_count == 2  # 누적
    # 일별 리포트에도 스냅샷 첨부.
    assert res.day_results[0].report.portfolio_snapshot is not None


def test_real_orders_zero_across_days():
    res = _run(
        [
            _day("d1", ["NVDA"], {"NVDA": _ctx(10, 100.0)}),
            _day("d2", ["AAPL"], {"AAPL": _ctx(5, 200.0)}),
        ],
        account_cash=100_000.0,
    )
    assert res.real_orders_placed == 0
    assert all(r.real_orders_placed == 0 for r in res.day_results)
    assert all(r.report.orders_placed == 0 for r in res.day_results)


def test_empty_days_returns_initial_portfolio():
    res = _run([], account_cash=50_000.0)
    assert res.day_results == ()
    assert res.portfolio.cash == 50_000.0
    assert res.real_orders_placed == 0
