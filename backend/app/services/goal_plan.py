"""목표 플랜 서비스 — 결정론 역산 세팅 + AI 근거 결합 (ADR-001/003/005).

step 0의 `algorithms.goal_planner.derive_settings`가 계산한 결정론 세팅에 AI 근거/요약을
붙여 `GoalPlan`을 만든다. 외부 API(Claude) 호출은 이 service 레이어에 격리한다(ADR-001).

CRITICAL (ADR-003/005): 세팅 수치의 단일 진실은 `algorithms.goal_planner`다. provider(AI)는
근거 텍스트만 생성하고, provider가 무엇을 반환하든 `GoalPlan.settings`는 `derive_settings`
결과 그대로다. AI 환각이 하드캡(특히 max_risk_pct)을 우회해 위험 세팅을 만들지 못하게 한다.

provider 주입 패턴은 agents/decision.py · algorithms/filters.py의 Mock/Claude 골격과 일치한다.

spec: specs/goal_plan_service.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from algorithms.goal_planner import (
    Feasibility,
    GoalDerivedSettings,
    PlanMode,
    derive_settings,
)

# provider 예외 시 근거 텍스트 대체 문구(서비스가 죽지 않게).
_FALLBACK_RATIONALE = "AI 근거 생성에 실패해 결정론 세팅만 제공합니다(근거 생략)."


# --- 데이터 모델 ---


@dataclass(frozen=True)
class GoalInput:
    """목표 플랜 입력."""

    current_equity: float
    target_amount: float
    months: int
    mode: PlanMode


class GoalPlan(BaseModel):
    """목표 플랜 — 결정론 세팅 + AI 근거. API 직렬화 대상.

    settings/RiskLimits는 stdlib frozen dataclass이므로 재검증/coerce 없이 그대로 담는다
    (수치 단일 진실 보존).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    settings: GoalDerivedSettings
    rationale: str
    summary: str
    feasibility: Feasibility
    required_monthly_return: float


# --- 근거 provider (외부 의존 주입 — ADR-005) ---


@runtime_checkable
class GoalPlanProvider(Protocol):
    """근거 텍스트 생성 인터페이스. 구현은 Mock/Claude로 분기."""

    async def explain(self, inp: GoalInput, settings: GoalDerivedSettings) -> str: ...


class MockGoalPlanProvider:
    """결정론적 템플릿 근거 provider (TDD용).

    세팅 수치를 사람이 읽을 문장으로 옮길 뿐, 수치를 바꾸지 않는다. 난수·외부 호출 없음.
    """

    async def explain(self, inp: GoalInput, settings: GoalDerivedSettings) -> str:
        return (
            f"월 {settings.required_monthly_return * 100:.1f}% 필요, "
            f"모드 {inp.mode.value}, 실현가능성 {settings.feasibility.value} → "
            f"appetite {settings.appetite:.2f}, "
            f"risk {settings.risk_limits.max_risk_pct * 100:.1f}%."
        )


class ClaudeGoalPlanProvider:
    """실제 Claude(claude-sonnet-4-6) 근거 생성 연동 골격.

    이 step에서는 로직을 채우지 않는다(키/연동은 후속 phase). 키가 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError.

    실제 연동 시 구조(주석):
        # client = anthropic.Anthropic(api_key=self._api_key)
        # msg = client.messages.create(
        #     model="claude-sonnet-4-6",
        #     max_tokens=...,
        #     messages=[{"role": "user", "content":
        #         <목표/기간/모드 + derive_settings 결과를 요약 설명하라는 프롬프트>}],
        # )
        # → 응답 텍스트를 근거(rationale)로 반환. 세팅 수치는 절대 바꾸지 않는다(ADR-003/005).
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def explain(self, inp: GoalInput, settings: GoalDerivedSettings) -> str:
        if not self._api_key:
            raise ValueError(
                "Claude API 키가 없다. 근거 생성 불가 (후속 phase에서 연동)."
            )
        raise NotImplementedError(
            "Claude 근거 연동은 후속 phase에서 구현한다. "
            "현재는 키가 있어도 실호출하지 않는다."
        )


# --- 조립 헬퍼 ---


async def _safe_explain(
    provider: GoalPlanProvider, inp: GoalInput, settings: GoalDerivedSettings
) -> str:
    """provider 근거를 생성하되, 예외 시 fallback 문구로 격리한다.

    provider가 죽어도 결정론 세팅은 영향받지 않는다(호출자가 settings를 따로 보유).
    """
    try:
        return await provider.explain(inp, settings)
    except Exception:  # noqa: BLE001 — 근거는 부가 정보. 실패해도 세팅은 안전하게 반환.
        return _FALLBACK_RATIONALE


def _build_summary(settings: GoalDerivedSettings) -> str:
    """세팅으로부터 결정론적 요약을 만든다. 비현실적 목표면 경고를 덧붙인다."""
    base = (
        f"필요 월 수익률 {settings.required_monthly_return * 100:.1f}%, "
        f"실현가능성 {settings.feasibility.value}, "
        f"최대 리스크 {settings.risk_limits.max_risk_pct * 100:.1f}%."
    )
    if settings.feasibility is Feasibility.UNREALISTIC:
        return (
            f"{base} 경고: 비현실적 목표입니다 — 리스크는 시스템 한도로 제한되며, "
            "목표 달성이 보장되지 않습니다."
        )
    return base


# --- 공개 서비스 함수 ---


async def generate_goal_plan(
    inp: GoalInput, provider: GoalPlanProvider
) -> GoalPlan:
    """결정론 역산 세팅에 AI 근거를 결합해 GoalPlan을 만든다.

    ① derive_settings로 결정론 세팅(하드캡 적용) ② provider.explain로 근거(예외 시 fallback)
    ③ 결정론 요약 조립. provider 반환값/예외는 settings 수치에 영향을 주지 않는다(ADR-003/005).
    입력 무효(current_equity<=0/months<=0)면 derive_settings에서 ValueError 전파.
    """
    settings = derive_settings(
        inp.current_equity, inp.target_amount, inp.months, inp.mode
    )
    rationale = await _safe_explain(provider, inp, settings)
    summary = _build_summary(settings)

    return GoalPlan(
        settings=settings,
        rationale=rationale,
        summary=summary,
        feasibility=settings.feasibility,
        required_monthly_return=settings.required_monthly_return,
    )
