"""Phase 1 flow ↔ 시뮬 포트폴리오 통합 테스트 (spec: specs/sim_portfolio.md, phase1_flow.md).

단일 포트폴리오를 후보 전체에 공유: 누적 갱신, 뒤 후보가 줄어든 현금을 봄, vetoed 불변, 불가능 주문
차단, 리포트에 스냅샷. real_orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.base import AgentRegistry
from agents.decision import MockDecisionProvider
from agents.phase1_flow import CandidateContext, run_phase1_dry_run
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


def _run(symbols, contexts, *, account_cash):
    policy = load_policy(REAL_CONFIG)
    return asyncio.run(run_phase1_dry_run(
        scanner=_scanner(symbols), decision_provider=MockDecisionProvider(), policy=policy,
        account_phase="1", risk_mode_name="B", regime_name="NORMAL_BULL",
        compass_state="strong", contexts=contexts, report_date="2026-06-19",
        account_cash=account_cash,
    ))


# --- 공유 포트폴리오 누적 ---


def test_multiple_candidates_update_one_shared_portfolio():
    res = _run(
        ["NVDA", "AAPL"],
        {"NVDA": _ctx(10, 100.0), "AAPL": _ctx(5, 200.0)},
        account_cash=100_000.0,
    )
    pf = res.portfolio
    assert pf is not None
    assert set(pf.positions) == {"NVDA", "AAPL"}            # 둘 다 체결
    assert len(pf.trade_log) == 2
    # 현금은 두 체결만큼 감소(슬리피지 포함 ≈ 2000+).
    assert pf.cash < 100_000.0 - 1990.0
    assert pf.total_exposure() > 0
    assert res.real_orders_placed == 0


def test_second_candidate_sees_reduced_cash_and_is_blocked():
    # 시작 현금 1500: 첫 후보(≈1001) 체결 후 ~499 남음 → 둘째(≈1001) 현금부족으로 차단.
    res = _run(
        ["NVDA", "AAPL"],
        {"NVDA": _ctx(10, 100.0), "AAPL": _ctx(10, 100.0)},
        account_cash=1500.0,
    )
    pf = res.portfolio
    assert len(res.simulated_orders) == 1                    # 하나만 체결
    assert "NVDA" in pf.positions and "AAPL" not in pf.positions
    assert pf.cash < 1500.0 and pf.cash > 0
    assert res.real_orders_placed == 0


def test_vetoed_candidate_does_not_change_state():
    res = _run(
        ["NVDA", "AAPL"],
        {"NVDA": _ctx(10, 100.0), "AAPL": _ctx(10, 100.0, data_ok=False)},  # AAPL veto
        account_cash=100_000.0,
    )
    pf = res.portfolio
    assert set(pf.positions) == {"NVDA"}                     # vetoed AAPL 미반영
    assert len(pf.trade_log) == 1
    assert res.report.riskgate_vetoes == 1
    assert res.real_orders_placed == 0


def test_final_report_includes_portfolio_snapshot():
    res = _run(["NVDA"], {"NVDA": _ctx(10, 100.0)}, account_cash=100_000.0)
    snap = res.report.portfolio_snapshot
    assert snap is not None
    assert snap.starting_cash == 100_000.0
    assert snap.cash < 100_000.0
    assert snap.open_positions == 1
    assert snap.open_symbols == ("NVDA",)
    assert snap.trade_count == 1
    assert snap.real_orders_placed == 0


def test_no_portfolio_when_no_account_cash():
    res = _run(["NVDA"], {"NVDA": _ctx(10, 100.0)}, account_cash=None)
    assert res.portfolio is None
    assert res.report.portfolio_snapshot is None
    assert res.real_orders_placed == 0


def test_real_orders_remain_zero_with_portfolio():
    res = _run(["NVDA", "AAPL"], {"NVDA": _ctx(10, 100.0), "AAPL": _ctx(5, 200.0)}, account_cash=100_000.0)
    assert res.real_orders_placed == 0
    assert res.report.orders_placed == 0
    assert res.portfolio.real_orders_placed == 0
