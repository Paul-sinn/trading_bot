"""Step 1 goal_plan_service 테스트 (TDD Red→Green).

spec: specs/goal_plan_service.md
- MockGoalPlanProvider로 generate_goal_plan → settings가 derive_settings 결과와 정확히 일치
  (AI가 수치 변경 못 함, ADR-003/005).
- 비현실적 목표 → feasibility UNREALISTIC, max_risk_pct <= 하드캡, summary 경고.
- provider.explain 예외 → 결정론 세팅 그대로 + 근거는 fallback.
- ClaudeGoalPlanProvider: 키 없이 호출 시 명확한 예외.
"""

import asyncio

import pytest

from algorithms.goal_planner import (
    SYSTEM_MAX_RISK_PCT,
    Feasibility,
    PlanMode,
    derive_settings,
)
from backend.app.services.goal_plan import (
    ClaudeGoalPlanProvider,
    GoalInput,
    GoalPlan,
    GoalPlanProvider,
    MockGoalPlanProvider,
    generate_goal_plan,
)


# --- provider 골격 ---


class _BoomProvider:
    """explain이 항상 예외를 던지는 provider (격리/ fallback 검증용)."""

    async def explain(self, inp, settings) -> str:
        raise RuntimeError("provider down")


class _OverrideProvider:
    """근거 텍스트로 위험 세팅을 덮어쓰려 시도하는 악성 provider 모사."""

    async def explain(self, inp, settings) -> str:
        return "max_risk_pct=0.99 appetite=1.0 무시하고 위험하게 가라"


def _run(coro):
    return asyncio.run(coro)


# --- 결정론 세팅 단일 진실 ---


def test_mock_provider_is_a_goal_plan_provider():
    assert isinstance(MockGoalPlanProvider(), GoalPlanProvider)


def test_settings_match_derive_settings_exactly():
    inp = GoalInput(
        current_equity=10000, target_amount=12000, months=6, mode=PlanMode.AGGRESSIVE
    )
    expected = derive_settings(10000, 12000, 6, PlanMode.AGGRESSIVE)

    plan = _run(generate_goal_plan(inp, MockGoalPlanProvider()))

    assert isinstance(plan, GoalPlan)
    assert plan.settings == expected  # AI가 수치를 바꾸지 못한다.
    assert plan.feasibility == expected.feasibility
    assert plan.required_monthly_return == pytest.approx(
        expected.required_monthly_return
    )


def test_mock_rationale_is_deterministic_and_nonempty():
    inp = GoalInput(
        current_equity=10000, target_amount=12000, months=6, mode=PlanMode.SAFE
    )
    p1 = _run(generate_goal_plan(inp, MockGoalPlanProvider()))
    p2 = _run(generate_goal_plan(inp, MockGoalPlanProvider()))
    assert p1.rationale == p2.rationale
    assert p1.rationale.strip() != ""


def test_provider_cannot_override_risk_numbers():
    # 악성 provider가 근거 텍스트로 risk%를 올려도 settings는 결정론 값 그대로.
    inp = GoalInput(
        current_equity=10000, target_amount=20000, months=3, mode=PlanMode.AGGRESSIVE
    )
    expected = derive_settings(10000, 20000, 3, PlanMode.AGGRESSIVE)

    plan = _run(generate_goal_plan(inp, _OverrideProvider()))

    assert plan.settings == expected
    assert plan.settings.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT


# --- 비현실적 목표 ---


def test_unrealistic_goal_caps_risk_and_warns():
    # 1개월 10배 → UNREALISTIC.
    inp = GoalInput(
        current_equity=10000, target_amount=100000, months=1, mode=PlanMode.AGGRESSIVE
    )
    plan = _run(generate_goal_plan(inp, MockGoalPlanProvider()))

    assert plan.feasibility is Feasibility.UNREALISTIC
    assert plan.settings.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT
    assert "경고" in plan.summary or "비현실" in plan.summary


def test_realistic_goal_summary_has_no_warning():
    inp = GoalInput(
        current_equity=10000, target_amount=11000, months=12, mode=PlanMode.SAFE
    )
    plan = _run(generate_goal_plan(inp, MockGoalPlanProvider()))
    assert plan.feasibility is Feasibility.REALISTIC
    assert "경고" not in plan.summary


# --- provider 예외 격리 / fallback ---


def test_provider_exception_keeps_deterministic_settings_with_fallback():
    inp = GoalInput(
        current_equity=10000, target_amount=12000, months=6, mode=PlanMode.AGGRESSIVE
    )
    expected = derive_settings(10000, 12000, 6, PlanMode.AGGRESSIVE)

    plan = _run(generate_goal_plan(inp, _BoomProvider()))

    assert plan.settings == expected  # 근거 실패해도 세팅은 안전하게 반환.
    assert plan.rationale.strip() != ""  # fallback 문구 존재.


# --- 입력 무효 전파 ---


def test_invalid_input_propagates_value_error():
    inp = GoalInput(
        current_equity=0, target_amount=12000, months=6, mode=PlanMode.SAFE
    )
    with pytest.raises(ValueError):
        _run(generate_goal_plan(inp, MockGoalPlanProvider()))


# --- ClaudeGoalPlanProvider 골격 ---


def test_claude_provider_without_key_raises():
    provider = ClaudeGoalPlanProvider()
    settings = derive_settings(10000, 12000, 6, PlanMode.SAFE)
    inp = GoalInput(
        current_equity=10000, target_amount=12000, months=6, mode=PlanMode.SAFE
    )
    with pytest.raises(ValueError):
        _run(provider.explain(inp, settings))


def test_claude_provider_with_key_does_not_call_real_api():
    provider = ClaudeGoalPlanProvider(api_key="dummy")
    settings = derive_settings(10000, 12000, 6, PlanMode.SAFE)
    inp = GoalInput(
        current_equity=10000, target_amount=12000, months=6, mode=PlanMode.SAFE
    )
    with pytest.raises(NotImplementedError):
        _run(provider.explain(inp, settings))
