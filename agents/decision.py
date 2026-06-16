"""판단 에이전트 — 후보 종합 판단(매수/홀드/매도).

spec: specs/decision_agent.md

스캐너가 만든 후보(Candidate)에 대해 차트 시그널·필터 결과·(있으면) 뉴스 요약을 종합해
매수(BUY)/홀드(HOLD)/매도(SELL)를 결정한다. 알고리즘 3레이어를 모두 통과한 후보만 여기로
오며, 이 에이전트가 자동매매 루프의 최종 판단 게이트다.

원칙:
- ADR-005: Claude는 최종 게이트, 알고리즘이 1차 필터. Claude 의존은 DecisionProvider 주입으로
  격리하고, 이 phase에서는 결정론적 MockDecisionProvider만 사용한다(키 부재 + 비결정론).
- ADR-002: 시그널/필터 판정을 여기서 재구현하지 않는다. Candidate에 담긴 Layer 1/2 결과를
  활용만 한다(단일 진실).
- 안전 최우선: 불확실(provider 예외)하면 매매(BUY/SELL)가 아니라 보수적 HOLD로 떨어뜨린다.
- 격리: 한 후보의 provider 예외가 전체 판단 루프를 막지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from agents.base import Agent, AgentRegistry
from agents.scanner import Candidate
from algorithms.signals import Signal

# 결정론 confidence 고정값(MockDecisionProvider).
_BUY_CONFIDENCE = 0.8
_HOLD_CONFIDENCE = 0.5


class Decision(str, Enum):
    """최종 판단."""

    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


# --- 데이터 모델 ---


@dataclass(frozen=True)
class DecisionInput:
    """판단 입력 — 후보 + 부가 맥락."""

    candidate: Candidate
    context: dict


@dataclass(frozen=True)
class DecisionResult:
    """판단 결과. confidence는 0~1로 클램프된다."""

    decision: Decision
    confidence: float
    rationale: str


# --- 판단 provider (외부 의존 주입) ---


@runtime_checkable
class DecisionProvider(Protocol):
    """판단 조회 인터페이스. 구현은 Mock/Claude로 분기."""

    async def decide(self, inp: DecisionInput) -> DecisionResult: ...


class MockDecisionProvider:
    """결정론적 판단 provider (TDD용).

    규칙: 후보 시그널 overall == BULLISH 이고 filters_passed == True → BUY, 아니면 HOLD.
    난수·외부 호출 없음. 불확실/약세는 매매하지 않고 HOLD로 둔다(보수적).
    """

    async def decide(self, inp: DecisionInput) -> DecisionResult:
        cand = inp.candidate
        if cand.signal.overall is Signal.BULLISH and cand.filters_passed:
            return DecisionResult(
                Decision.BUY,
                _BUY_CONFIDENCE,
                "Layer1 BULLISH + Layer2 필터 통과 → 매수.",
            )
        return DecisionResult(
            Decision.HOLD,
            _HOLD_CONFIDENCE,
            "BULLISH & 필터통과 동시 충족 아님 → 홀드(보수적).",
        )


class ClaudeDecisionProvider:
    """실제 Claude(claude-sonnet-4-6) 판단 연동 골격.

    이 step에서는 로직을 채우지 않는다(키/연동은 후속 phase). 키가 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError.

    실제 연동 시 구조(주석):
        # client = anthropic.Anthropic(api_key=self._api_key)
        # msg = client.messages.create(
        #     model="claude-sonnet-4-6",
        #     max_tokens=...,
        #     messages=[{"role": "user", "content": <차트 시그널 + 필터 + 뉴스 요약 프롬프트>}],
        # )
        # → 응답을 파싱해 DecisionResult(decision, confidence, rationale)로 변환.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def decide(self, inp: DecisionInput) -> DecisionResult:
        if not self._api_key:
            raise ValueError(
                "Claude API 키가 없다. 판단 조회 불가 (후속 phase에서 연동)."
            )
        raise NotImplementedError(
            "Claude 판단 연동은 후속 phase에서 구현한다. "
            "현재는 키가 있어도 실호출하지 않는다."
        )


# --- 결과 변환 헬퍼 ---


def _clamp_confidence(value: float) -> float:
    """confidence를 0~1로 클램프한다."""
    return max(0.0, min(1.0, float(value)))


def _normalize(result: DecisionResult) -> DecisionResult:
    """provider 결과의 confidence를 0~1로 클램프해 정규화한다."""
    clamped = _clamp_confidence(result.confidence)
    if clamped == result.confidence:
        return result
    return DecisionResult(result.decision, clamped, result.rationale)


def _safe_hold(candidate: Candidate, reason: str) -> DecisionResult:
    """불확실/예외 시 보수적 안전 기본값(HOLD, confidence 0)."""
    return DecisionResult(Decision.HOLD, 0.0, f"{candidate.symbol}: {reason}")


# --- 판단 에이전트 (상태 루프) ---


class DecisionAgent(Agent):
    """후보를 종합 판단해 매수/홀드/매도 결과 리스트를 만든다.

    판단 로직(차트+뉴스 종합)은 주입된 DecisionProvider에 위임하고, 이 클래스는 격리·보수적
    안전 처리·클램프만 담당한다.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        provider: DecisionProvider,
        *,
        name: str = "decision",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.provider = provider
        self.latest_results: list[DecisionResult] = []

    async def decide_candidates(
        self, candidates: list[Candidate]
    ) -> list[DecisionResult]:
        """후보 리스트를 판단해 결과 리스트를 반환한다.

        registry가 kill 상태면 즉시 빈 리스트(판단 스킵). 한 후보에서 provider 예외가 나면
        그 후보만 보수적 HOLD로 처리하고 나머지는 정상 판단한다(격리 + 안전 기본값).
        confidence는 0~1로 클램프한다.
        """
        if self.registry.is_killed():
            return []

        results: list[DecisionResult] = []
        for candidate in candidates:
            inp = DecisionInput(candidate=candidate, context=dict(candidate.detail))
            try:
                result = await self.provider.decide(inp)
            except Exception:  # noqa: BLE001 — 불확실 시 매매하지 않고 보수적 HOLD로 격리.
                results.append(_safe_hold(candidate, "provider 예외 → 보수적 HOLD."))
                continue
            results.append(_normalize(result))
        return results

    async def tick(self) -> None:
        """루프 1회. 현재 step은 후보 소스 연결 전이므로 빈 입력으로 동작한다.

        registry가 kill 상태면 자연히 빈 결과. 후속 step에서 스캐너 결과와 배선한다.
        """
        self.latest_results = await self.decide_candidates([])
