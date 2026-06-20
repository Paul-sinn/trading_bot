"""Phase 1 엔드투엔드 dry-run 통합 테스트 (spec: specs/phase1_flow.md).

scanner → decision → weight 제안 → hard-veto → simulated order → dry-run report 배선을 검증한다.
happy path(시뮬 주문 생성) + veto path(주문 없음) + Tier5(자동 집중 금지) + real_orders=0.
실 ScannerAgent + MockDecisionProvider + 실 config Policy 사용. 네트워크/브로커 없음.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest

from agents.base import AgentRegistry
from agents.decision import Decision, MockDecisionProvider
from agents.phase1_flow import (
    CandidateContext,
    Phase1Result,
    run_phase1_dry_run,
)
from agents.policy_loader import load_policy
from agents.scanner import MockPriceDataProvider, ScannerAgent
from algorithms.regime import Regime

from pathlib import Path

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _make_df(prices) -> pd.DataFrame:
    prices = np.array(prices, dtype=float)
    vol = np.full(len(prices), 1000.0)
    vol[-1] = 5000.0
    return pd.DataFrame(
        {"open": prices, "high": prices * 1.005, "low": prices * 0.995,
         "close": prices, "volume": vol}
    )


def _bullish_df() -> pd.DataFrame:
    return _make_df(np.linspace(80, 200, 260))


def _scanner(symbols) -> ScannerAgent:
    frames = {s: _bullish_df() for s in symbols}
    return ScannerAgent(AgentRegistry(), MockPriceDataProvider(frames), list(symbols))


def _clean_ctx(**ov) -> CandidateContext:
    base = dict(
        stop_loss_pct=0.05, per_trade_risk_pct=0.04, regime=Regime.NORMAL_BULL,
        quantity=10, liquidity_ok=True, tier_exposure_ok=True, data_ok=True,
        ipo_data_ok=True, event_risk_checked=True, technical_confirmation=True,
        manual_override=False,
    )
    base.update(ov)
    return CandidateContext(**base)


def _run(**kw):
    policy = load_policy(REAL_CONFIG)
    defaults = dict(
        decision_provider=MockDecisionProvider(),
        policy=policy,
        account_phase="1",
        risk_mode_name="B",
        regime_name="NORMAL_BULL",
        compass_state="strong",
        report_date="2026-06-19",
    )
    defaults.update(kw)
    return asyncio.run(run_phase1_dry_run(**defaults))


# --- happy path ---


def test_happy_path_creates_simulated_order():
    res = _run(scanner=_scanner(["NVDA"]), contexts={"NVDA": _clean_ctx()})
    assert isinstance(res, Phase1Result)
    assert len(res.simulated_orders) == 1
    assert res.simulated_orders[0].symbol == "NVDA"
    assert res.real_orders_placed == 0
    assert res.report.orders_placed == 0
    assert res.report.riskgate_vetoes == 0
    assert "NVDA" in res.report.review_buys
    # weight 제안: Tier1 → phase1 tier_0_2 하단 0.80.
    assert res.weight_suggestions["NVDA"].suggested_weight == pytest.approx(0.80)


def test_happy_path_row_effective_buy_matches_sim_order():
    res = _run(scanner=_scanner(["NVDA"]), contexts={"NVDA": _clean_ctx()})
    row = next(d for d in res.report.decisions if d.symbol == "NVDA")
    assert row.raw_decision is Decision.BUY
    assert row.effective_decision is Decision.BUY
    # 불변: 시뮬 주문 존재 ⟺ effective BUY
    assert (len(res.simulated_orders) == 1) == (row.effective_decision is Decision.BUY)


# --- veto path ---


def test_veto_path_creates_no_order():
    res = _run(scanner=_scanner(["NVDA"]), contexts={"NVDA": _clean_ctx(liquidity_ok=False)})
    assert res.simulated_orders == ()
    assert res.real_orders_placed == 0
    assert res.report.riskgate_vetoes == 1
    row = next(d for d in res.report.decisions if d.symbol == "NVDA")
    assert row.raw_decision is Decision.BUY
    assert row.effective_decision is Decision.HOLD  # veto → 강등
    assert "NVDA" not in res.report.review_buys


def test_needs_adjustment_self_corrects_to_safe_weight():
    # stop 0.10: tier_0_2 원안 0.80 → account_loss 0.08 > 0.07 → 0.70으로 축소 제안.
    # 오케스트레이터가 축소 비중(0.70)을 쓰므로 account_loss=0.07=캡 → 통과 → 안전 사이즈로 주문 생성.
    # (제안이 리스크 캡을 절대 넘지 않게 스스로 줄인다 — 넘는 비중은 주문이 안 생긴다.)
    res = _run(scanner=_scanner(["NVDA"]), contexts={"NVDA": _clean_ctx(stop_loss_pct=0.10)})
    sug = res.weight_suggestions["NVDA"]
    assert sug.status == "needs_adjustment"
    assert sug.suggested_weight == pytest.approx(0.70)
    assert len(res.simulated_orders) == 1     # 축소된 안전 비중으로 생성
    assert res.real_orders_placed == 0


def test_no_safe_weight_blocks_order():
    # 제안이 구체 비중을 못 주는 경우(Tier5 small_only)만 weight 없음 → fail-closed veto → 주문 없음.
    res = _run(scanner=_scanner(["SOUN"]), contexts={"SOUN": _clean_ctx(stop_loss_pct=0.20)})
    assert res.simulated_orders == ()
    assert res.weight_suggestions["SOUN"].suggested_weight is None


# --- Tier 5: 자동 집중 금지 ---


def test_tier5_candidate_creates_no_order():
    # SOUN = Tier5(watch). weight small_only(None) → fail-closed veto → 시뮬 주문 없음.
    res = _run(scanner=_scanner(["SOUN"]), contexts={"SOUN": _clean_ctx()})
    assert res.simulated_orders == ()
    assert res.weight_suggestions["SOUN"].status == "small_only"
    assert res.real_orders_placed == 0


# --- 컨텍스트 없음 / 빈 후보 ---


def test_missing_context_vetoed_no_order():
    res = _run(scanner=_scanner(["NVDA"]), contexts={})  # 컨텍스트 없음
    assert res.simulated_orders == ()
    assert res.report.riskgate_vetoes == 1


def test_no_candidates_empty_report():
    res = _run(scanner=_scanner([]), contexts={})
    assert res.simulated_orders == ()
    assert res.report.orders_placed == 0
    assert res.report.decisions == ()


# --- 혼합: 통과 1 + veto 1 ---


def test_mixed_pass_and_veto():
    res = _run(
        scanner=_scanner(["NVDA", "AAPL"]),
        contexts={"NVDA": _clean_ctx(), "AAPL": _clean_ctx(data_ok=False)},
    )
    assert len(res.simulated_orders) == 1
    assert res.simulated_orders[0].symbol == "NVDA"
    assert res.report.riskgate_vetoes == 1
    assert res.real_orders_placed == 0


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        _run(scanner=_scanner(["NVDA"]), contexts={"NVDA": _clean_ctx()}, risk_mode_name="Z")
