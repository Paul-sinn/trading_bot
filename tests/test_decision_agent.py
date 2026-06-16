"""Step 3 decision-agent 테스트 (TDD Red→Green).

spec: specs/decision_agent.md
- MockDecisionProvider: BULLISH & filters_passed → BUY, 아니면 HOLD (결정론적).
- decide_candidates: 다건 처리, 빈 후보 → 빈 결과.
- provider 예외 후보 → HOLD(안전 기본값), 다른 후보는 정상.
- registry.killed=True → 판단 스킵(빈 리스트).
- ClaudeDecisionProvider: 키 없이 호출 시 명확한 예외.
- confidence 0~1 클램프.
"""

import asyncio

import pytest

from agents.base import AgentRegistry
from agents.decision import (
    ClaudeDecisionProvider,
    Decision,
    DecisionAgent,
    DecisionInput,
    DecisionProvider,
    DecisionResult,
    MockDecisionProvider,
)
from agents.scanner import Candidate
from algorithms.signals import Signal, SignalResult


# --- 결정론적 Candidate 헬퍼 ---


def _signal(overall: Signal) -> SignalResult:
    return SignalResult(ema=overall, rsi=overall, macd=overall, overall=overall)


def _candidate(symbol: str, overall: Signal, filters_passed: bool) -> Candidate:
    sig = _signal(overall)
    return Candidate(
        symbol=symbol,
        signal=sig,
        filters_passed=filters_passed,
        detail={"signal": sig},
    )


def _bull() -> Candidate:
    return _candidate("BULL", Signal.BULLISH, True)


def _neutral() -> Candidate:
    return _candidate("NEUT", Signal.NEUTRAL, True)


def _agent(registry=None, provider=None) -> DecisionAgent:
    return DecisionAgent(
        registry or AgentRegistry(),
        provider or MockDecisionProvider(),
    )


# --- MockDecisionProvider 결정론 ---


def test_mock_provider_bullish_passed_is_buy():
    provider = MockDecisionProvider()
    inp = DecisionInput(candidate=_bull(), context={})
    res = asyncio.run(provider.decide(inp))
    assert res.decision is Decision.BUY


def test_mock_provider_not_bullish_is_hold():
    provider = MockDecisionProvider()
    inp = DecisionInput(candidate=_neutral(), context={})
    res = asyncio.run(provider.decide(inp))
    assert res.decision is Decision.HOLD


def test_mock_provider_filters_failed_is_hold():
    provider = MockDecisionProvider()
    cand = _candidate("BULL", Signal.BULLISH, False)
    res = asyncio.run(provider.decide(DecisionInput(candidate=cand, context={})))
    assert res.decision is Decision.HOLD


def test_mock_provider_is_a_decision_provider():
    assert isinstance(MockDecisionProvider(), DecisionProvider)


# --- decide_candidates 다건/빈 입력 ---


def test_decide_candidates_multiple():
    agent = _agent()
    results = asyncio.run(agent.decide_candidates([_bull(), _neutral()]))
    assert [r.decision for r in results] == [Decision.BUY, Decision.HOLD]


def test_decide_candidates_empty_returns_empty():
    agent = _agent()
    assert asyncio.run(agent.decide_candidates([])) == []


def test_results_are_decision_results():
    agent = _agent()
    results = asyncio.run(agent.decide_candidates([_bull()]))
    assert all(isinstance(r, DecisionResult) for r in results)


# --- provider 예외 → HOLD(안전 기본값) ---


class _BoomProvider:
    """특정 심볼에서 예외를 던지는 provider(격리/보수 처리 검증용)."""

    def __init__(self, boom_symbol: str) -> None:
        self._boom = boom_symbol

    async def decide(self, inp: DecisionInput) -> DecisionResult:
        if inp.candidate.symbol == self._boom:
            raise RuntimeError("provider 실패")
        return DecisionResult(Decision.BUY, 0.9, "ok")


def test_provider_exception_candidate_is_hold():
    agent = _agent(provider=_BoomProvider("BAD"))
    bad = _candidate("BAD", Signal.BULLISH, True)
    good = _candidate("GOOD", Signal.BULLISH, True)
    results = asyncio.run(agent.decide_candidates([bad, good]))
    by_symbol = {c.symbol: r for c, r in zip([bad, good], results)}
    assert by_symbol["BAD"].decision is Decision.HOLD
    assert by_symbol["GOOD"].decision is Decision.BUY


# --- registry killed → 스킵 ---


def test_decide_skipped_when_registry_killed():
    registry = AgentRegistry()
    registry.kill_all("리스크 한도 초과")
    agent = _agent(registry=registry)
    assert asyncio.run(agent.decide_candidates([_bull()])) == []


# --- tick() ---


def test_tick_stores_latest_results():
    agent = _agent()
    asyncio.run(agent.tick())
    assert agent.latest_results == []


# --- confidence 클램프 ---


class _OutOfRangeProvider:
    async def decide(self, inp: DecisionInput) -> DecisionResult:
        return DecisionResult(Decision.BUY, 1.5, "over")


def test_confidence_clamped_to_unit_range():
    agent = _agent(provider=_OutOfRangeProvider())
    results = asyncio.run(agent.decide_candidates([_bull()]))
    assert results[0].confidence == 1.0


# --- ClaudeDecisionProvider 골격 ---


def test_claude_provider_without_key_raises():
    provider = ClaudeDecisionProvider(api_key=None)
    with pytest.raises(ValueError):
        asyncio.run(provider.decide(DecisionInput(candidate=_bull(), context={})))


def test_claude_provider_with_key_not_implemented():
    provider = ClaudeDecisionProvider(api_key="sk-test")
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.decide(DecisionInput(candidate=_bull(), context={})))
