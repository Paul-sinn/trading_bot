"""자동 trailing-high 추적 테스트 (spec: specs/sim_exit.md).

일별 종가로 보유 포지션의 trailing_high를 단조 갱신 → 트레일링 스탑이 갱신된 고점을 사용. 가격 결측
fail-closed. real orders=0. 전략 미변경. 네트워크/브로커 없음.
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
from agents.sim_exit import ExitParams, ExitReason, apply_exit
from agents.sim_portfolio import SimulatedPortfolio
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _fill(symbol="NVDA", shares=10, price=100.0):
    return SimulatedFill(
        symbol=symbol, side="buy", intended_notional=shares * price, estimated_shares=shares,
        reference_price=price, slippage_pct=0.0, fill_price=price,
        filled_notional=shares * price, cash_remaining=0.0,
    )


def _pf():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    return pf


# --- trailing_high 추적 ---


def test_trailing_high_initialized_at_entry():
    pf = _pf()
    assert pf.positions["NVDA"].trailing_high == pytest.approx(100.0)


def test_trailing_high_increases_on_new_high():
    pf = _pf()
    pf.update_trailing_highs({"NVDA": 110.0})
    assert pf.positions["NVDA"].trailing_high == pytest.approx(110.0)
    pf.update_trailing_highs({"NVDA": 125.0})
    assert pf.positions["NVDA"].trailing_high == pytest.approx(125.0)


def test_trailing_high_does_not_decrease():
    pf = _pf()
    pf.update_trailing_highs({"NVDA": 130.0})
    pf.update_trailing_highs({"NVDA": 105.0})            # 하락
    assert pf.positions["NVDA"].trailing_high == pytest.approx(130.0)  # 유지
    pf.update_trailing_highs({"NVDA": 90.0})
    assert pf.positions["NVDA"].trailing_high == pytest.approx(130.0)


def test_missing_price_does_not_update_and_is_reported():
    pf = _pf()
    pf.update_trailing_highs({"NVDA": 120.0})
    missing = pf.update_trailing_highs({})               # 가격 결측
    assert missing == ("NVDA",)
    assert pf.positions["NVDA"].trailing_high == pytest.approx(120.0)  # 불변
    # NaN/0도 결측 취급.
    assert pf.update_trailing_highs({"NVDA": float("nan")}) == ("NVDA",)
    assert pf.update_trailing_highs({"NVDA": 0.0}) == ("NVDA",)


# --- 트레일링 스탑이 갱신된 고점 사용 ---


def test_trailing_stop_uses_updated_trailing_high():
    pf = _pf()
    pf.update_trailing_highs({"NVDA": 120.0})            # 고점 120
    # trail 10% → 임계 108. price 105 ≤ 108 → 청산. (명시 trailing_high 없이 추적값 사용)
    res = apply_exit(pf, "NVDA", price=105.0, params=ExitParams(trail_pct=0.10))
    assert res.exited is True and res.reason is ExitReason.TRAILING_STOP_HIT
    assert "NVDA" not in pf.positions


def test_trailing_stop_not_hit_without_high_update():
    pf = _pf()  # 고점 100, 임계 90
    res = apply_exit(pf, "NVDA", price=105.0, params=ExitParams(trail_pct=0.10))
    assert res.exited is False                           # 105 > 90 → 미청산
    assert pf.positions["NVDA"].shares == 10


def test_trailing_missing_price_fail_closed():
    pf = _pf()
    pf.update_trailing_highs({"NVDA": 120.0})
    res = apply_exit(pf, "NVDA", price=None, params=ExitParams(trail_pct=0.10))
    assert res.exited is False and res.data_missing is True
    assert "NVDA" in pf.positions
    assert pf.real_orders_placed == 0


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


def _ctx(quantity=10, reference_price=100.0, **ov):
    base = dict(
        stop_loss_pct=0.05, per_trade_risk_pct=0.04, regime=Regime.NORMAL_BULL,
        quantity=quantity, reference_price=reference_price,
        trend_confirmed=True, volume_confirmed=True, relative_strength_confirmed=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return CandidateContext(**base)


def test_multiday_trailing_high_tracks_then_exits():
    # d1 매수(고점≈entry). d2 종가 130(고점 갱신, 미청산). d3 종가 110 + trail 10%(임계 117) → 청산.
    days = [
        DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 100.0}),
        DayInput("d2", _scanner([]), {}, mark_prices={"NVDA": 130.0},
                 exits={"NVDA": ExitParams(trail_pct=0.10)}),   # 130 > 117 → 미청산
        DayInput("d3", _scanner([]), {}, mark_prices={"NVDA": 110.0},
                 exits={"NVDA": ExitParams(trail_pct=0.10)}),   # 110 ≤ 117(130*0.9) → 청산
    ]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    # d2: 미청산, d3: 트레일링 청산.
    assert res.day_exits[1][0].exited is False
    assert res.day_exits[2][0].exited is True
    assert res.day_exits[2][0].reason is ExitReason.TRAILING_STOP_HIT
    assert "NVDA" not in res.portfolio.positions
    assert res.real_orders_placed == 0
