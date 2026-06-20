"""fill 시뮬레이션 + 슬리피지 테스트 (spec: specs/fill.md).

순수 모델(estimate_slippage_pct, simulate_fill) + SimulatedExecutor 연결(주문→체결, RiskGate 게이트).
요청 항목: pass 후보 체결 / vetoed 무체결 / 매수 슬리피지 체결가↑ / real_orders=0. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import pytest

from agents.decision import Decision, MockDecisionProvider
from agents.evidence import EvidenceParams, MockEventRiskProvider, build_contexts
from agents.fill import (
    FillContext,
    SimulatedFill,
    estimate_slippage_pct,
    simulate_fill,
)
from agents.phase1_flow import run_phase1_dry_run
from agents.policy_loader import load_policy
from agents.sim_execution import SimulatedExecutor, SimulatedOrder
from algorithms.policy import RiskMode, TierEntry, UniversePolicy, VetoInput
from algorithms.regime import Regime

import numpy as np
import pandas as pd
from agents.base import AgentRegistry
from agents.scanner import MockPriceDataProvider, ScannerAgent

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_PASS_GATE = lambda: (True, "ok")


# --- 순수 슬리피지 모델 ---


def test_slippage_default_when_no_spread():
    assert estimate_slippage_pct() == pytest.approx(0.0010)


def test_slippage_half_spread_floored_at_default():
    # 큰 spread → 하프스프레드. 작은 spread여도 안전 디폴트 하한(보수적).
    assert estimate_slippage_pct(spread_pct=0.01) == pytest.approx(0.005)   # 0.01/2
    assert estimate_slippage_pct(spread_pct=0.0001) == pytest.approx(0.0010)  # 디폴트 하한


def test_slippage_participation_adds_impact_and_caps():
    s = estimate_slippage_pct(participation=0.2)  # 0.0010 + 0.10*0.2 = 0.021
    assert s == pytest.approx(0.0010 + 0.02)
    assert estimate_slippage_pct(participation=100.0) == pytest.approx(0.05)  # max_pct 캡


# --- 순수 체결 ---


def _order(qty=10):
    return SimulatedOrder("NVDA", "buy", qty)


def test_simulate_fill_buy_slippage_increases_price():
    ctx = FillContext(reference_price=100.0, account_cash=100_000.0)
    fill = simulate_fill(_order(10), ctx)
    assert isinstance(fill, SimulatedFill)
    assert fill.fill_price > fill.reference_price           # 매수 슬리피지 → 체결가↑
    assert fill.intended_notional == pytest.approx(1000.0)  # 10 × 100
    assert fill.estimated_shares == 10
    assert fill.filled_notional == pytest.approx(10 * fill.fill_price)
    assert fill.cash_remaining == pytest.approx(100_000.0 - fill.filled_notional)


def test_simulate_fill_uses_spread_when_available():
    ctx = FillContext(reference_price=100.0, account_cash=100_000.0, spread_pct=0.02)
    fill = simulate_fill(_order(10), ctx)
    assert fill.slippage_pct == pytest.approx(0.01)         # 0.02/2
    assert fill.fill_price == pytest.approx(101.0)


# --- SimulatedExecutor 연결: pass → 체결 / veto → 무체결 ---


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


_FILL_CTX = FillContext(reference_price=100.0, account_cash=100_000.0)


def test_pass_candidate_receives_simulated_fill():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(), Decision.BUY, 10, fill_context=_FILL_CTX)
    assert res.created is True
    assert res.fill is not None
    assert res.fill.fill_price > 100.0
    assert len(ex.simulated_fills) == 1
    assert ex.real_orders_placed == 0


def test_vetoed_candidate_receives_no_fill():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(liquidity_ok=False), Decision.BUY, 10, fill_context=_FILL_CTX)
    assert res.created is False
    assert res.fill is None
    assert ex.simulated_fills == ()
    assert ex.real_orders_placed == 0


def test_order_without_fill_context_has_no_fill():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    res = ex.submit(_clean_input(), Decision.BUY, 10)  # fill_context 없음
    assert res.created is True
    assert res.fill is None
    assert ex.simulated_fills == ()
    assert len(ex.simulated_orders) == 1


def test_real_orders_zero_with_fills():
    ex = SimulatedExecutor(global_gate=_PASS_GATE)
    ex.submit(_clean_input(), Decision.BUY, 10, fill_context=_FILL_CTX)
    assert ex.real_orders_placed == 0


# --- phase1_flow 통합: 완전 자동 + 체결 ---


def _df(prices, volume=1_000_000.0):
    prices = np.array(prices, dtype=float)
    vol = np.full(len(prices), volume)
    vol[-1] = volume * 5
    return pd.DataFrame(
        {"open": prices, "high": prices * 1.005, "low": prices * 0.995,
         "close": prices, "volume": vol}
    )


def test_phase1_full_flow_produces_fill():
    policy = load_policy(REAL_CONFIG)
    scanner = ScannerAgent(AgentRegistry(), MockPriceDataProvider({"NVDA": _df(np.linspace(80, 200, 260))}), ["NVDA"])
    spy = pd.Series(np.linspace(300, 400, 260))
    bench = pd.Series(np.linspace(100, 110, 260))

    async def _go():
        cands = await scanner.scan()
        contexts = await build_contexts(
            cands, scanner.price_provider, spy_prices=spy, vix=15.0,
            params=EvidenceParams(account_equity=100_000.0),
            benchmark_prices=bench, event_provider=MockEventRiskProvider(default=True),
        )
        return await run_phase1_dry_run(
            scanner=scanner, decision_provider=MockDecisionProvider(), policy=policy,
            account_phase="1", risk_mode_name="B", regime_name="NORMAL_BULL",
            compass_state="strong", contexts=contexts, report_date="2026-06-19",
            account_cash=100_000.0,
        )

    res = asyncio.run(_go())
    assert len(res.simulated_orders) == 1
    assert len(res.simulated_fills) == 1
    assert res.simulated_fills[0].symbol == "NVDA"
    assert res.simulated_fills[0].fill_price > res.simulated_fills[0].reference_price
    assert res.real_orders_placed == 0
