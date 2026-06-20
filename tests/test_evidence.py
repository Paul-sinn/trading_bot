"""evidence 자동 구성 테스트 (spec: specs/evidence.md).

scanner/데이터 출력에서 CandidateContext를 자동 파생: 완전 증거(통과) / 결측 증거(fail-closed) /
veto path(phase1_flow 통합). 전략 시그널 로직 미변경 — 재사용만. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.base import AgentRegistry
from agents.evidence import (
    EvidenceParams,
    EventRiskProvider,
    MockEventRiskProvider,
    build_candidate_context,
    build_contexts,
)
from agents.phase1_flow import CandidateContext, run_phase1_dry_run
from agents.policy_loader import load_policy
from agents.scanner import MockPriceDataProvider, ScannerAgent
from agents.decision import Decision, MockDecisionProvider
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _df(prices, volume=1_000_000.0) -> pd.DataFrame:
    prices = np.array(prices, dtype=float)
    vol = np.full(len(prices), volume)
    vol[-1] = volume * 5
    return pd.DataFrame(
        {"open": prices, "high": prices * 1.005, "low": prices * 0.995,
         "close": prices, "volume": vol}
    )


def _bullish(volume=1_000_000.0):
    return _df(np.linspace(80, 200, 260), volume)


_SPY_BULL = pd.Series(np.linspace(300, 400, 260))   # >200d MA, 상승
_SPY_BEAR = pd.Series(np.linspace(400, 300, 260))   # <200d MA
_BENCH_WEAK = pd.Series(np.linspace(100, 110, 260))  # 종목이 아웃퍼폼 → rs True


def _params(**ov):
    base = dict(account_equity=100_000.0, min_dollar_volume=1e7)
    base.update(ov)
    return EvidenceParams(**base)


def _candidate(symbol="NVDA", df=None):
    df = df if df is not None else _bullish()
    scanner = ScannerAgent(AgentRegistry(), MockPriceDataProvider({symbol: df}), [symbol])
    cands = asyncio.run(scanner.scan())
    assert cands, "스캐너가 후보를 못 만듦(테스트 전제 깨짐)"
    return cands[0], df


# --- 완전 증거 → 모두 통과 ---


def test_complete_evidence_all_confirmed():
    cand, df = _candidate()
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BULL, vix=15.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=MockEventRiskProvider(default=True),
    )
    assert isinstance(ctx, CandidateContext)
    assert ctx.trend_confirmed is True
    assert ctx.volume_confirmed is True
    assert ctx.relative_strength_confirmed is True
    assert ctx.technical_confirmation is True       # 셋의 AND
    assert ctx.liquidity_ok is True
    assert ctx.data_ok is True
    assert ctx.event_risk_checked is True
    assert ctx.regime is Regime.NORMAL_BULL
    assert ctx.quantity > 0
    assert ctx.stop_loss_pct > 0
    assert ctx.per_trade_risk_pct <= 0.05           # ADR-003 캡 이내


# --- 결측 증거 → fail-closed ---


def test_missing_event_provider_fails_closed():
    cand, df = _candidate()
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BULL, vix=15.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=None,
    )
    assert ctx.event_risk_checked is False


def test_missing_benchmark_makes_rs_false():
    cand, df = _candidate()
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BULL, vix=15.0, params=_params(),
        benchmark_prices=None, event_provider=MockEventRiskProvider(default=True),
    )
    assert ctx.relative_strength_confirmed is False
    assert ctx.technical_confirmation is False       # rs False → AND False


def test_low_liquidity_fails_closed():
    cand, df = _candidate(df=_bullish(volume=50.0))  # ADV 작음
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BULL, vix=15.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=MockEventRiskProvider(default=True),
    )
    assert ctx.liquidity_ok is False


def test_bad_data_quality_invalidates_sizing():
    cand, _ = _candidate()
    bad = _bullish()
    bad.loc[bad.index[-1], "close"] = np.nan  # 최근 봉 NaN
    ctx = build_candidate_context(
        cand, bad, spy_prices=_SPY_BULL, vix=15.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=MockEventRiskProvider(default=True),
    )
    assert ctx.data_ok is False
    assert ctx.quantity == 0
    assert ctx.stop_loss_pct == 0.0
    assert ctx.per_trade_risk_pct == float("inf")    # fail-closed


def test_bearish_spy_yields_bearish_regime():
    cand, df = _candidate()
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BEAR, vix=15.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=MockEventRiskProvider(default=True),
    )
    assert ctx.regime is Regime.BEARISH


def test_panic_vix_yields_panic_regime():
    cand, df = _candidate()
    ctx = build_candidate_context(
        cand, df, spy_prices=_SPY_BULL, vix=40.0, params=_params(),
        benchmark_prices=_BENCH_WEAK, event_provider=MockEventRiskProvider(default=True),
    )
    assert ctx.regime is Regime.PANIC


def test_mock_event_provider_is_protocol():
    assert isinstance(MockEventRiskProvider(default=True), EventRiskProvider)


# --- phase1_flow 통합: 완전 증거 happy / 결측 veto ---


def _full_auto_run(symbols, *, event_provider, benchmark, spy):
    policy = load_policy(REAL_CONFIG)
    scanner = ScannerAgent(
        AgentRegistry(),
        MockPriceDataProvider({s: _bullish() for s in symbols}),
        list(symbols),
    )

    async def _go():
        cands = await scanner.scan()
        contexts = await build_contexts(
            cands, scanner.price_provider, spy_prices=spy, vix=15.0,
            params=_params(), benchmark_prices=benchmark, event_provider=event_provider,
        )
        return await run_phase1_dry_run(
            scanner=scanner, decision_provider=MockDecisionProvider(), policy=policy,
            account_phase="1", risk_mode_name="B", regime_name="NORMAL_BULL",
            compass_state="strong", contexts=contexts, report_date="2026-06-19",
        )

    return asyncio.run(_go())


def test_full_automation_happy_path_creates_order():
    res = _full_auto_run(
        ["NVDA"], event_provider=MockEventRiskProvider(default=True),
        benchmark=_BENCH_WEAK, spy=_SPY_BULL,
    )
    assert len(res.simulated_orders) == 1
    assert res.simulated_orders[0].symbol == "NVDA"
    assert res.real_orders_placed == 0
    assert "NVDA" in res.report.review_buys


def test_full_automation_veto_when_event_missing():
    res = _full_auto_run(
        ["NVDA"], event_provider=None, benchmark=_BENCH_WEAK, spy=_SPY_BULL,
    )
    assert res.simulated_orders == ()                 # event 결측 → veto → 주문 없음
    assert res.report.riskgate_vetoes == 1
    assert res.real_orders_placed == 0


def test_full_automation_veto_when_bearish_regime():
    res = _full_auto_run(
        ["NVDA"], event_provider=MockEventRiskProvider(default=True),
        benchmark=_BENCH_WEAK, spy=_SPY_BEAR,
    )
    assert res.simulated_orders == ()                 # regime risk-off → veto
