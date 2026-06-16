"""리포트 에이전트 — 체결 집계 → 일간/주간 성과 + AI 코멘트 → DB 저장.

spec: specs/reporter_agent.md

실행 에이전트가 남긴 체결(Fill)을 모아 일간/주간 성과(총손익·승률·거래수)를 집계하고,
AI 코멘트(이 phase는 mock)를 붙여 SQLite(SQLAlchemy)에 저장한다.

원칙:
- ADR-002: 집계는 부수효과 없는 순수 함수(aggregate_daily/weekly). DB I/O는 에이전트/세션
  레이어에만 둔다. 지표/손익 계산을 다른 곳에서 재구현하지 않는다.
- ADR-004: 개발 DB는 SQLite. 세션은 주입(테스트는 인메모리로 격리, 파일 DB 오염 금지).
- ADR-005: Claude 의존은 CommentProvider 주입으로 격리. 이 phase는 결정론적 MockCommentProvider만
  사용한다(ClaudeCommentProvider는 골격 + 명확한 예외까지).
- 안전/무결성: 빈 fills에서도 분모 0(ZeroDivision) 없이 안전하게 0 통계를 낸다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from agents.base import Agent, AgentRegistry
from agents.executor import Fill
from backend.app.db.models import DailyReport


# --- 집계 통계 모델 ---


@dataclass(frozen=True)
class DailyStats:
    """일간 집계 결과."""

    total_pnl: float
    win_rate: float
    trade_count: int


@dataclass(frozen=True)
class WeeklyStats:
    """주간 집계 결과."""

    total_pnl: float
    win_rate: float
    trade_count: int


# --- 집계 순수 함수 ---


def _summarize(fills: list[Fill]) -> tuple[float, float, int]:
    """fills를 (total_pnl, win_rate, trade_count)로 집계한다(순수, 분모 0 안전).

    승 = realized_pnl > 0(breakeven 0은 승 아님). trade_count==0이면 win_rate=0.0.
    """
    trade_count = len(fills)
    total_pnl = sum(f.realized_pnl for f in fills)
    if trade_count == 0:
        return 0.0, 0.0, 0
    wins = sum(1 for f in fills if f.realized_pnl > 0)
    return total_pnl, wins / trade_count, trade_count


def aggregate_daily(fills: list[Fill]) -> DailyStats:
    """일간 체결을 집계한다(순수). 호출측이 그날의 fills를 전달한다."""
    total_pnl, win_rate, trade_count = _summarize(fills)
    return DailyStats(total_pnl=total_pnl, win_rate=win_rate, trade_count=trade_count)


def aggregate_weekly(fills: list[Fill]) -> WeeklyStats:
    """주간 체결을 집계한다(순수). 주간 그룹핑은 호출측 책임(대칭)."""
    total_pnl, win_rate, trade_count = _summarize(fills)
    return WeeklyStats(total_pnl=total_pnl, win_rate=win_rate, trade_count=trade_count)


# --- 코멘트 provider (외부 의존 주입) ---


@runtime_checkable
class CommentProvider(Protocol):
    """성과 코멘트 인터페이스. 구현은 Mock/Claude로 분기."""

    async def comment(self, stats) -> str: ...


class MockCommentProvider:
    """결정론적 코멘트 provider (TDD용).

    stats(총손익·승률·거래수)를 반영한 템플릿 문자열을 반환한다. 난수·외부 호출 없음.
    동일 stats → 동일 코멘트.
    """

    async def comment(self, stats) -> str:
        tone = "수익" if stats.total_pnl > 0 else "손실" if stats.total_pnl < 0 else "보합"
        return (
            f"[{tone}] 거래 {stats.trade_count}건, "
            f"총손익 {stats.total_pnl:.2f}, 승률 {stats.win_rate:.0%}."
        )


class ClaudeCommentProvider:
    """실제 Claude(claude-sonnet-4-6) 코멘트 연동 골격.

    이 step에서는 로직을 채우지 않는다(키/연동은 후속 phase). 키가 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError.

    실제 연동 시 구조(주석):
        # client = anthropic.Anthropic(api_key=self._api_key)
        # msg = client.messages.create(
        #     model="claude-sonnet-4-6",
        #     max_tokens=...,
        #     messages=[{"role": "user", "content": <stats 요약 프롬프트>}],
        # )
        # return msg.content[0].text
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def comment(self, stats) -> str:
        if not self._api_key:
            raise ValueError(
                "Claude API 키가 없다. 코멘트 생성 불가 (후속 phase에서 연동)."
            )
        raise NotImplementedError(
            "Claude 코멘트 연동은 후속 phase에서 구현한다. "
            "현재는 키가 있어도 실호출하지 않는다."
        )


# --- 리포트 에이전트 (상태 루프) ---


class ReporterAgent(Agent):
    """체결을 집계하고 AI 코멘트를 붙여 일간/주간 리포트를 DB에 저장한다.

    집계는 순수 함수(aggregate_*)에 위임하고, 이 클래스는 코멘트 조회·영속화(I/O)만 담당한다.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        session_factory: Callable[[], Session],
        comment_provider: CommentProvider,
        *,
        name: str = "reporter",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.session_factory = session_factory
        self.comment_provider = comment_provider

    async def generate_daily(
        self, fills: list[Fill], report_date: date | None = None
    ) -> DailyReport:
        """일간 리포트를 생성한다: 집계 → 코멘트 → DB 저장 → 반환.

        report_date 미지정 시 오늘 날짜를 사용한다.
        """
        stats = aggregate_daily(fills)
        comment = await self.comment_provider.comment(stats)
        report = DailyReport(
            date=report_date or date.today(),
            total_pnl=stats.total_pnl,
            win_rate=stats.win_rate,
            trade_count=stats.trade_count,
            ai_comment=comment,
        )
        with self.session_factory() as session:
            session.add(report)
            session.commit()
            session.refresh(report)
            session.expunge(report)
        return report

    async def tick(self) -> None:
        """루프 1회. 현재 step은 체결 소스 연결 전이므로 no-op.

        후속 step에서 실행 에이전트의 fills와 스케줄(매일 9시)에 배선한다.
        """
        return None
