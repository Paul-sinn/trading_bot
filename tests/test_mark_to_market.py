"""일별 mark-to-market / 미실현 PnL 테스트 (spec: specs/multiday.md, sim_portfolio.md).

보유 포지션을 일별 종가로 평가 → 시가/미실현 PnL/equity 갱신. 가격 결측은 fail-closed(data_missing).
multi-day 이월. real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.base import AgentRegistry
from agents.fill import SimulatedFill
from agents.multiday import DayInput, run_phase1_multiday
from agents.phase1_flow import CandidateContext
from agents.policy_loader import load_policy
from agents.scanner import MockPriceDataProvider, ScannerAgent
from agents.sim_portfolio import SimulatedPortfolio
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


# --- 포트폴리오 스냅샷 단위(mark-to-market) ---


def _fill(symbol="NVDA", shares=10, price=100.0):
    return SimulatedFill(
        symbol=symbol, side="buy", intended_notional=shares * price, estimated_shares=shares,
        reference_price=price, slippage_pct=0.0, fill_price=price,
        filled_notional=shares * price, cash_remaining=0.0,
    )


def test_open_position_value_updates_with_price():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    snap = pf.snapshot({"NVDA": 110.0})
    assert snap.market_value == pytest.approx(1100.0)        # 10 × 110
    assert snap.unrealized_pnl == pytest.approx(100.0)       # (110-100)×10
    assert snap.data_missing is False


def test_total_equity_equals_cash_plus_market_value():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    snap = pf.snapshot({"NVDA": 130.0})
    assert snap.equity == pytest.approx(snap.cash + snap.market_value)
    assert snap.equity == pytest.approx(99_000.0 + 1300.0)


def test_missing_price_is_handled_safely():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    snap = pf.snapshot({})                                   # NVDA 가격 결측
    assert snap.data_missing is True
    assert snap.unrealized_pnl == 0.0                        # 가짜 손익 금지
    assert snap.market_value == pytest.approx(1000.0)        # cost_basis 폴백
    # 가격 전무(None)도 안전.
    snap_none = pf.snapshot(None)
    assert snap_none.data_missing is True
    assert snap_none.real_orders_placed == 0


def test_no_positions_not_data_missing():
    pf = SimulatedPortfolio(100_000.0)
    snap = pf.snapshot({})
    assert snap.data_missing is False
    assert snap.market_value == 0.0


# --- multi-day mark-to-market ---


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


def test_unrealized_pnl_updates_across_days():
    # Day1: NVDA 10주 @~100 매수. Day2: 신규 매수 없이 종가 120으로 mark.
    days = [
        DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 110.0}),
        DayInput("d2", _scanner([]), {}, mark_prices={"NVDA": 120.0}),
    ]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    snaps = res.daily_snapshots
    avg = res.portfolio.positions["NVDA"].avg_entry_price
    assert snaps[0].unrealized_pnl == pytest.approx((110.0 - avg) * 10)
    assert snaps[1].unrealized_pnl == pytest.approx((120.0 - avg) * 10)
    assert snaps[1].unrealized_pnl > snaps[0].unrealized_pnl      # 가격↑ → 미실현↑
    assert snaps[1].equity == pytest.approx(snaps[1].cash + snaps[1].market_value)
    assert res.real_orders_placed == 0


def test_multiday_missing_price_marks_data_missing():
    days = [
        DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 110.0}),
        DayInput("d2", _scanner([]), {}, mark_prices={}),         # 종가 결측
    ]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    assert res.daily_snapshots[0].data_missing is False
    assert res.daily_snapshots[1].data_missing is True
    assert res.real_orders_placed == 0


def test_real_orders_zero_with_marking():
    days = [DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 110.0})]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    assert res.real_orders_placed == 0
    assert res.day_results[0].report.portfolio_snapshot.real_orders_placed == 0
