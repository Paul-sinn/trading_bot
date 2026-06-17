"""SQLAlchemy 모델 — 거래기록 · 일간/주간 리포트.

spec: specs/reporter_agent.md

서버 상태(권위)는 SQLite(개발)/PostgreSQL(프로덕션)에 둔다(ARCHITECTURE / ADR-004).
이 모듈은 모델(스키마)만 정의한다 — 엔진/세션은 `session.py`, 집계 순수 함수는 `agents/reporter.py`.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """선언적 베이스. 모든 모델이 상속한다."""


class TradeRecord(Base):
    """단일 거래기록 — 진입/청산가와 실현 손익, AI 메모."""

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ai_memo: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class DailyReport(Base):
    """일간 성과 리포트 — 총손익·승률·거래수 + AI 코멘트."""

    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_comment: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class GoalPlanRecord(Base):
    """적용된 목표 플랜 레코드 — 결정론 세팅(수치 단일 진실은 algorithms.goal_planner) + AI 근거.

    생성(`POST /api/goal-plan`)은 저장하지 않는다. 사용자가 검토 후 적용할 때만 저장하며,
    `applied=True` 활성 레코드는 1건만 유지한다(직전 활성은 False로 내려간다).
    spec: specs/goal_plan_api.md
    """

    __tablename__ = "goal_plan_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_amount: Mapped[float] = mapped_column(Float, nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    required_monthly_return: Mapped[float] = mapped_column(Float, nullable=False)
    feasibility: Mapped[str] = mapped_column(String(16), nullable=False)
    appetite: Mapped[float] = mapped_column(Float, nullable=False)
    # max_risk_pct는 서비스가 SYSTEM_MAX_RISK_PCT 하드캡으로 clamp한 값 그대로(ADR-003).
    max_risk_pct: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    max_position_pct: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss_atr_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class WeeklyReport(Base):
    """주간 성과 리포트 — 총손익·승률·거래수 + AI 코멘트."""

    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_comment: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
