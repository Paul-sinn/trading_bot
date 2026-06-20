"""sim_portfolio 테스트 (spec: specs/sim_portfolio.md).

시뮬 체결 → 현금/포지션/노출/PnL/로그 추적 + 불가능 주문 방지. SimulatedExecutor 연결로 vetoed →
포트폴리오 불변. real_orders=0. 네트워크/브로커 없음.
"""

import pytest

from agents.decision import Decision
from agents.fill import FillContext, SimulatedFill
from agents.sim_execution import SimulatedExecutor
from agents.sim_portfolio import (
    PortfolioGuardConfig,
    SimulatedPortfolio,
    SimulatedPosition,
)
from algorithms.policy import RiskMode, TierEntry, UniversePolicy, VetoInput
from algorithms.regime import Regime

_PASS_GATE = lambda: (True, "ok")


def _fill(symbol="NVDA", shares=10, price=100.0, cash=100_000.0) -> SimulatedFill:
    return SimulatedFill(
        symbol=symbol, side="buy", intended_notional=shares * price,
        estimated_shares=shares, reference_price=price, slippage_pct=0.0,
        fill_price=price, filled_notional=shares * price, cash_remaining=cash - shares * price,
    )


# --- 포트폴리오 단위 ---


def test_buy_fill_reduces_cash():
    pf = SimulatedPortfolio(100_000.0)
    res = pf.apply_buy_fill(_fill(shares=10, price=100.0))
    assert res.applied is True
    assert pf.cash == pytest.approx(100_000.0 - 1000.0)
    assert pf.starting_cash == 100_000.0


def test_buy_fill_creates_and_updates_position():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill(shares=10, price=100.0))
    pos = pf.positions["NVDA"]
    assert isinstance(pos, SimulatedPosition)
    assert pos.shares == 10 and pos.avg_entry_price == pytest.approx(100.0)
    # 추가 매수 → 평단 갱신.
    pf.apply_buy_fill(_fill(shares=10, price=120.0))
    pos = pf.positions["NVDA"]
    assert pos.shares == 20
    assert pos.avg_entry_price == pytest.approx(110.0)  # (1000+1200)/20


def test_insufficient_cash_rejects_fill():
    pf = SimulatedPortfolio(500.0)
    res = pf.apply_buy_fill(_fill(shares=10, price=100.0))  # 1000 > 500
    assert res.applied is False
    assert "현금" in res.reason
    assert pf.cash == 500.0                # 상태 불변
    assert pf.positions == {}


def test_exposure_calculated_correctly():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))   # cost 1000
    pf.apply_buy_fill(_fill("AAPL", 5, 200.0))    # cost 1000
    assert pf.total_exposure() == pytest.approx(2000.0)         # cost basis
    assert pf.equity() == pytest.approx(100_000.0)             # cash 98000 + 2000
    # 라이브 가격 주면 시가 기준.
    prices = {"NVDA": 110.0, "AAPL": 190.0}
    assert pf.total_exposure(prices) == pytest.approx(10 * 110 + 5 * 190)
    assert pf.unrealized_pnl(prices) == pytest.approx((110 - 100) * 10 + (190 - 200) * 5)


def test_position_cap_exceeded_rejects():
    pf = SimulatedPortfolio(100_000.0, guards=PortfolioGuardConfig(max_position_pct=0.05))
    # 10000 notional / equity 100000 = 10% > 5% → 거부.
    res = pf.apply_buy_fill(_fill("NVDA", 100, 100.0))
    assert res.applied is False
    assert pf.positions == {}


def test_tier_exposure_cap_rejects():
    pf = SimulatedPortfolio(
        100_000.0, guards=PortfolioGuardConfig(tier_exposure_caps={"5": 0.02})
    )
    # Tier5 6000 / equity 100000 = 6% > 2% → 거부.
    res = pf.apply_buy_fill(_fill("SOUN", 60, 100.0), tier="5")
    assert res.applied is False
    assert pf.positions == {}
    # 한도 내(1000=1%)는 통과.
    ok = pf.apply_buy_fill(_fill("SOUN", 10, 100.0), tier="5")
    assert ok.applied is True


def test_no_add_to_position_when_disabled():
    pf = SimulatedPortfolio(100_000.0, guards=PortfolioGuardConfig(allow_add_to_position=False))
    assert pf.apply_buy_fill(_fill("NVDA", 10, 100.0)).applied is True
    assert pf.apply_buy_fill(_fill("NVDA", 10, 100.0)).applied is False  # 중복 추가 금지


def test_trade_log_records_fills():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    assert len(pf.trade_log) == 1
    assert pf.trade_log[0].symbol == "NVDA" and pf.trade_log[0].side == "buy"


def test_realized_pnl_on_sell():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    res = pf.apply_sell_fill("NVDA", 10, 130.0)   # (130-100)*10 = 300
    assert res.applied is True
    assert pf.realized_pnl == pytest.approx(300.0)
    assert "NVDA" not in pf.positions
    assert pf.real_orders_placed == 0


def test_real_orders_placed_zero():
    pf = SimulatedPortfolio(100_000.0)
    pf.apply_buy_fill(_fill("NVDA", 10, 100.0))
    assert pf.real_orders_placed == 0


# --- SimulatedExecutor 연결 ---


def _mode_b() -> RiskMode:
    return RiskMode("B", 0.07, ("0", "1", "2", "3", "4A", "4B"), False, (), True)


def _universe() -> UniversePolicy:
    return UniversePolicy(entries=(TierEntry("NVDA", "1", ("1",), "approved", True, False),))


def _clean_input(**ov) -> VetoInput:
    base = dict(
        symbol="NVDA", mode=_mode_b(), universe=_universe(),
        per_trade_risk_pct=0.04, position_weight=0.5, stop_loss_pct=0.08,
        regime=Regime.NORMAL_BULL, has_stop_loss=True, position_size_ok=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return VetoInput(**base)


def test_executor_applies_fill_to_portfolio():
    pf = SimulatedPortfolio(100_000.0)
    ex = SimulatedExecutor(global_gate=_PASS_GATE, portfolio=pf)
    fc = FillContext(reference_price=100.0, account_cash=100_000.0)
    res = ex.submit(_clean_input(), Decision.BUY, 10, fill_context=fc)
    assert res.created is True
    assert pf.cash < 100_000.0                # 체결로 현금 감소
    assert "NVDA" in pf.positions
    assert ex.real_orders_placed == 0


def test_vetoed_candidate_does_not_affect_portfolio():
    pf = SimulatedPortfolio(100_000.0)
    ex = SimulatedExecutor(global_gate=_PASS_GATE, portfolio=pf)
    fc = FillContext(reference_price=100.0, account_cash=100_000.0)
    res = ex.submit(_clean_input(liquidity_ok=False), Decision.BUY, 10, fill_context=fc)
    assert res.created is False
    assert pf.cash == 100_000.0               # 불변
    assert pf.positions == {}
    assert ex.simulated_fills == ()
    assert ex.real_orders_placed == 0


def test_insufficient_cash_blocks_executor_order():
    pf = SimulatedPortfolio(500.0)            # 현금 부족
    ex = SimulatedExecutor(global_gate=_PASS_GATE, portfolio=pf)
    fc = FillContext(reference_price=100.0, account_cash=500.0)
    res = ex.submit(_clean_input(), Decision.BUY, 10, fill_context=fc)  # 1000 > 500
    assert res.created is False               # 불가능 주문 차단
    assert ex.simulated_orders == ()
    assert pf.positions == {}
    assert ex.real_orders_placed == 0
