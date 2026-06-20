"""시뮬 포지션 청산 테스트 (spec: specs/sim_exit.md).

stop/trailing/time/manual 청산, 부분/전량 → 현금·실현PnL·로그, 잔여 평단 유지, 가격 결측 fail-closed,
multiday 통합. real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
import math
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
from agents.sim_exit import (
    ExitParams,
    ExitReason,
    ExitResult,
    apply_exit,
    evaluate_exit,
)
from agents.sim_portfolio import SimulatedPortfolio
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _fill(symbol="NVDA", shares=10, price=100.0):
    return SimulatedFill(
        symbol=symbol, side="buy", intended_notional=shares * price, estimated_shares=shares,
        reference_price=price, slippage_pct=0.0, fill_price=price,
        filled_notional=shares * price, cash_remaining=0.0,
    )


def _pf_with_position(shares=10, price=100.0):
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", shares, price))
    return pf


# --- evaluate_exit (순수) ---


def test_evaluate_priority_manual_first():
    p = ExitParams(stop_price=95.0, manual_exit=True)
    d = evaluate_exit(price=90.0, shares_held=10, params=p)
    assert d.should_exit and d.reason is ExitReason.MANUAL_SIM_EXIT


def test_evaluate_missing_price_data_missing():
    for price in (None, float("nan"), 0.0, -1.0):
        d = evaluate_exit(price=price, shares_held=10, params=ExitParams(stop_price=95.0))
        assert d.should_exit is False and d.data_missing is True


# --- 청산 적용 ---


def test_stop_loss_closes_position():
    pf = _pf_with_position(10, 100.0)
    res = apply_exit(pf, "NVDA", price=90.0, params=ExitParams(stop_price=95.0))
    assert isinstance(res, ExitResult)
    assert res.exited is True and res.reason is ExitReason.STOP_LOSS_HIT
    assert "NVDA" not in pf.positions                     # 전량 청산
    assert pf.realized_pnl == pytest.approx((90.0 - 100.0) * 10)  # -100
    assert pf.cash == pytest.approx(99_000.0 + 900.0)
    assert pf.trade_log[-1].exit_reason == "stop_loss_hit"
    assert pf.real_orders_placed == 0


def test_trailing_stop_closes_position():
    pf = _pf_with_position(10, 100.0)
    # trailing_high 120, trail 10% → 임계 108. price 105 ≤ 108 → 청산.
    res = apply_exit(pf, "NVDA", price=105.0, params=ExitParams(trailing_high=120.0, trail_pct=0.10))
    assert res.exited is True and res.reason is ExitReason.TRAILING_STOP_HIT
    assert "NVDA" not in pf.positions
    assert pf.realized_pnl == pytest.approx((105.0 - 100.0) * 10)  # +50


def test_time_stop_closes_position():
    pf = _pf_with_position(10, 100.0)
    res = apply_exit(pf, "NVDA", price=110.0, params=ExitParams(hold_days=5, max_hold_days=5))
    assert res.exited is True and res.reason is ExitReason.TIME_STOP


def test_partial_exit_updates_cash_and_realized_and_keeps_avg():
    pf = _pf_with_position(10, 100.0)
    res = apply_exit(pf, "NVDA", price=130.0, params=ExitParams(manual_exit=True, exit_shares=4))
    assert res.exited is True and res.shares == 4
    pos = pf.positions["NVDA"]
    assert pos.shares == 6                                # 부분 청산
    assert pos.avg_entry_price == pytest.approx(100.0)    # 잔여 평단 유지
    assert pf.realized_pnl == pytest.approx((130.0 - 100.0) * 4)  # 120
    assert pf.cash == pytest.approx(99_000.0 + 4 * 130.0)


def test_remaining_position_unrealized_preserved():
    pf = _pf_with_position(10, 100.0)
    apply_exit(pf, "NVDA", price=130.0, params=ExitParams(manual_exit=True, exit_shares=4))
    snap = pf.snapshot({"NVDA": 130.0})
    assert snap.unrealized_pnl == pytest.approx((130.0 - 100.0) * 6)  # 잔여 6주
    assert snap.realized_pnl == pytest.approx(120.0)


def test_missing_price_fails_closed_no_exit():
    pf = _pf_with_position(10, 100.0)
    res = apply_exit(pf, "NVDA", price=None, params=ExitParams(stop_price=95.0))
    assert res.exited is False and res.data_missing is True
    assert "NVDA" in pf.positions                          # 미상가 매도 안 함
    assert pf.realized_pnl == 0.0
    assert pf.real_orders_placed == 0


def test_no_exit_when_conditions_not_met():
    pf = _pf_with_position(10, 100.0)
    res = apply_exit(pf, "NVDA", price=110.0, params=ExitParams(stop_price=95.0))
    assert res.exited is False
    assert pf.positions["NVDA"].shares == 10


def test_apply_exit_no_position():
    pf = SimulatedPortfolio(100_000.0)
    res = apply_exit(pf, "NVDA", price=100.0, params=ExitParams(manual_exit=True))
    assert res.exited is False


# --- multiday 통합 ---


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


def test_multiday_exit_closes_carried_position():
    # Day1: NVDA 매수. Day2: 신규 진입 없이 stop_loss로 청산.
    days = [
        DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 100.0}),
        DayInput(
            "d2", _scanner([]), {}, mark_prices={"NVDA": 90.0},
            exits={"NVDA": ExitParams(stop_price=95.0)},
        ),
    ]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    assert "NVDA" not in res.portfolio.positions          # Day2 청산
    assert res.day_exits[1][0].exited is True
    assert res.day_exits[1][0].reason is ExitReason.STOP_LOSS_HIT
    # 매매로그에 매수+매도 누적.
    sides = [t.side for t in res.trade_log]
    assert sides == ["buy", "sell"]
    assert res.real_orders_placed == 0
