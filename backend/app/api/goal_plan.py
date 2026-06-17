"""`/api/goal-plan` 목표 플랜 생성/적용 라우터.

생성(`POST /api/goal-plan`)은 결정론 세팅 + AI 근거(`GoalPlan`)를 만들어 반환만 한다 —
**DB·활성 세팅을 바꾸지 않는다**(검토 후 적용 원칙). 적용(`POST /api/goal-plan/apply`)은 동일
입력으로 플랜을 다시 생성해 `GoalPlanRecord`로 영속화하고, 활성(`applied=True`) 레코드를 1건만 유지한다.

CRITICAL (ADR-003): 세팅 수치의 단일 진실은 service(`generate_goal_plan` → `derive_settings`)다.
이 API 레이어는 수치를 재계산/변경하지 않는다. 적용도 클라이언트가 보낸 세팅을 신뢰하지 않고
입력으로부터 서비스를 다시 호출해 재생성한다(하드캡 우회 세팅 주입 차단). 외부 의존(Claude/Robinhood)은
service에 격리한다(ADR-001).

spec: specs/goal_plan_api.md
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from algorithms.goal_planner import PlanMode
from backend.app.db.models import GoalPlanRecord
from backend.app.db.session import make_session_factory
from backend.app.services.goal_plan import (
    GoalInput,
    GoalPlan,
    GoalPlanProvider,
    MockGoalPlanProvider,
    generate_goal_plan,
)
from backend.app.services.portfolio import (
    PortfolioProvider,
    get_portfolio_provider,
)

router = APIRouter()


# --- 요청/응답 DTO ---


class GoalPlanRequest(BaseModel):
    """목표 플랜 생성/적용 입력. 무효 입력은 422(pydantic 검증)."""

    target_amount: float = Field(gt=0)
    months: int = Field(gt=0)
    mode: PlanMode
    # 미제공/null → 포트폴리오 provider의 total_equity 사용.
    current_equity: float | None = Field(default=None, gt=0)


class GoalPlanRecordOut(BaseModel):
    """저장된 `GoalPlanRecord` 직렬화 DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    target_amount: float
    months: int
    mode: str
    required_monthly_return: float
    feasibility: str
    appetite: float
    max_risk_pct: float
    max_drawdown_pct: float
    max_position_pct: float
    stop_loss_atr_multiplier: float
    rationale: str | None
    applied: bool
    created_at: datetime


# --- 의존성 주입 (기본 Mock / 설정 DB; 테스트는 dependency_overrides로 교체) ---


def get_goal_plan_provider() -> GoalPlanProvider:
    """근거 provider. 기본 Mock(외부 호출 없음). 후속 phase에서 Claude로 교체."""
    return MockGoalPlanProvider()


def get_portfolio_provider_dep() -> PortfolioProvider:
    """포트폴리오 provider 주입(현재 equity 조회용).

    `get_portfolio_provider`를 인자 없이 감싼다 — 그 `settings: Settings` 파라미터가
    FastAPI에 body 필드로 노출돼 요청 body가 잘못 embed되는 것을 막는다.
    """
    return get_portfolio_provider()


# 앱이 쓰는 세션 팩토리(설정 DB, 기본 SQLite). 첫 사용 시 지연 생성한다.
# 이 프로젝트는 flat route 등록(`routes.extend`, main.py 참조)을 써서 FastAPI의
# app.dependency_overrides가 동작하지 않으므로, 테스트는 set_session_factory로 인메모리
# 팩토리를 주입/복원한다(파일 DB 오염 방지 — ADR-004).
_session_factory: Callable[[], Session] | None = None


def get_session_factory() -> Callable[[], Session]:
    """현재 세션 팩토리를 반환한다(미설정 시 설정 DB로 지연 생성)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory()
    return _session_factory


def set_session_factory(factory: Callable[[], Session] | None) -> None:
    """세션 팩토리를 교체한다. 테스트가 인메모리 팩토리 주입/복원에 사용한다."""
    global _session_factory
    _session_factory = factory


# --- 내부 헬퍼 ---


async def _resolve_input(
    req: GoalPlanRequest, portfolio_provider: PortfolioProvider
) -> GoalInput:
    """요청 → GoalInput. current_equity 미제공 시 포트폴리오 total_equity로 채운다."""
    equity = req.current_equity
    if equity is None:
        portfolio = await portfolio_provider.get_portfolio()
        equity = portfolio.total_equity
    return GoalInput(
        current_equity=equity,
        target_amount=req.target_amount,
        months=req.months,
        mode=req.mode,
    )


def _record_from_plan(req: GoalPlanRequest, plan: GoalPlan) -> GoalPlanRecord:
    """서비스 결과(GoalPlan) → ORM 레코드. 수치를 재계산하지 않고 그대로 옮긴다."""
    settings = plan.settings
    limits = settings.risk_limits
    return GoalPlanRecord(
        target_amount=req.target_amount,
        months=req.months,
        mode=req.mode.value,
        required_monthly_return=settings.required_monthly_return,
        feasibility=settings.feasibility.value,
        appetite=settings.appetite,
        max_risk_pct=limits.max_risk_pct,
        max_drawdown_pct=limits.max_drawdown_pct,
        max_position_pct=limits.max_position_pct,
        stop_loss_atr_multiplier=settings.stop_loss_atr_multiplier,
        rationale=plan.rationale,
    )


def _persist_active(
    record: GoalPlanRecord, session_factory: Callable[[], Session]
) -> GoalPlanRecord:
    """레코드를 활성(applied=True)으로 저장하고 기존 활성은 비활성으로 내린다(활성 1건 유지)."""
    with session_factory() as session:
        for prev in session.scalars(
            select(GoalPlanRecord).where(GoalPlanRecord.applied.is_(True))
        ):
            prev.applied = False
        record.applied = True
        session.add(record)
        session.commit()
        session.refresh(record)
        session.expunge(record)
    return record


# --- 엔드포인트 ---


@router.post("/api/goal-plan", response_model=GoalPlan)
async def create_goal_plan(
    req: GoalPlanRequest,
    provider: GoalPlanProvider = Depends(get_goal_plan_provider),
    portfolio_provider: PortfolioProvider = Depends(get_portfolio_provider_dep),
) -> GoalPlan:
    """목표 플랜을 생성한다(부수효과 없음 — DB·활성 세팅 불변, 검토 후 적용 원칙)."""
    inp = await _resolve_input(req, portfolio_provider)
    return await generate_goal_plan(inp, provider)


@router.post("/api/goal-plan/apply", response_model=GoalPlanRecordOut)
async def apply_goal_plan(
    req: GoalPlanRequest,
    provider: GoalPlanProvider = Depends(get_goal_plan_provider),
    portfolio_provider: PortfolioProvider = Depends(get_portfolio_provider_dep),
    session_factory: Callable[[], Session] = Depends(get_session_factory),
) -> GoalPlanRecord:
    """입력으로 플랜을 재생성(결정론·단일 진실)해 활성 세팅으로 영속화한다."""
    inp = await _resolve_input(req, portfolio_provider)
    plan = await generate_goal_plan(inp, provider)
    record = _record_from_plan(req, plan)
    return _persist_active(record, session_factory)
