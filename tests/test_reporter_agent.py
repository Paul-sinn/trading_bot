"""Step 5 reporter-agent 테스트 (TDD Red→Green).

spec: specs/reporter_agent.md
- aggregate_daily/weekly: 혼합 손익 → win_rate/total_pnl/trade_count 기댓값. 빈 fills 안전.
- generate_daily: 인메모리 SQLite에 DailyReport 저장 후 조회 검증(파일 DB 오염 금지).
- MockCommentProvider: stats를 반영한 결정론적 코멘트.
- 분모 0(빈 fills) ZeroDivision 없이 처리.
- ClaudeCommentProvider: 키 없으면 ValueError.
"""

import asyncio
from datetime import date

import pytest

from agents.base import AgentRegistry
from agents.executor import Fill
from agents.reporter import (
    ClaudeCommentProvider,
    CommentProvider,
    DailyStats,
    MockCommentProvider,
    ReporterAgent,
    WeeklyStats,
    aggregate_daily,
    aggregate_weekly,
)
from backend.app.db.models import DailyReport
from backend.app.db.session import make_session_factory


# --- fill 헬퍼 (테스트용) ---


def _fill(pnl: float, symbol: str = "AAPL") -> Fill:
    return Fill(
        symbol=symbol,
        side="sell",
        quantity=10,
        requested_price=100.0,
        filled_price=100.0 + pnl / 10,
        slippage=0.0,
        realized_pnl=pnl,
    )


# --- 집계 순수 함수 ---


def test_aggregate_daily_mixed_pnl():
    fills = [_fill(50.0), _fill(-20.0), _fill(30.0), _fill(-10.0)]
    stats = aggregate_daily(fills)
    assert isinstance(stats, DailyStats)
    assert stats.trade_count == 4
    assert stats.total_pnl == pytest.approx(50.0)
    # 승 2건(50, 30) / 4건 = 0.5
    assert stats.win_rate == pytest.approx(0.5)


def test_aggregate_daily_all_wins():
    stats = aggregate_daily([_fill(10.0), _fill(5.0)])
    assert stats.win_rate == pytest.approx(1.0)
    assert stats.total_pnl == pytest.approx(15.0)


def test_aggregate_daily_all_losses():
    stats = aggregate_daily([_fill(-10.0), _fill(-5.0)])
    assert stats.win_rate == pytest.approx(0.0)
    assert stats.total_pnl == pytest.approx(-15.0)


def test_aggregate_daily_breakeven_not_a_win():
    # realized_pnl == 0 은 승으로 세지 않는다.
    stats = aggregate_daily([_fill(0.0), _fill(10.0)])
    assert stats.trade_count == 2
    assert stats.win_rate == pytest.approx(0.5)


def test_aggregate_daily_empty_no_zero_division():
    stats = aggregate_daily([])
    assert stats.trade_count == 0
    assert stats.total_pnl == pytest.approx(0.0)
    assert stats.win_rate == pytest.approx(0.0)  # 분모 0 안전


def test_aggregate_weekly_symmetric():
    fills = [_fill(40.0), _fill(-10.0)]
    stats = aggregate_weekly(fills)
    assert isinstance(stats, WeeklyStats)
    assert stats.trade_count == 2
    assert stats.total_pnl == pytest.approx(30.0)
    assert stats.win_rate == pytest.approx(0.5)


def test_aggregate_weekly_empty_safe():
    stats = aggregate_weekly([])
    assert stats.trade_count == 0
    assert stats.win_rate == pytest.approx(0.0)


# --- MockCommentProvider ---


def test_mock_comment_reflects_stats_deterministic():
    provider = MockCommentProvider()
    assert isinstance(provider, CommentProvider)
    stats = DailyStats(total_pnl=50.0, win_rate=0.5, trade_count=4)
    a = asyncio.run(provider.comment(stats))
    b = asyncio.run(provider.comment(stats))
    assert a == b  # 결정론적
    # stats 수치를 반영한다.
    assert "4" in a  # trade_count
    assert "50" in a  # total_pnl


# --- generate_daily: 인메모리 SQLite 저장/조회 ---


def test_generate_daily_persists_to_memory_db():
    factory = make_session_factory("sqlite:///:memory:")
    agent = ReporterAgent(AgentRegistry(), factory, MockCommentProvider())
    fills = [_fill(50.0), _fill(-20.0), _fill(30.0)]

    report = asyncio.run(agent.generate_daily(fills, report_date=date(2026, 6, 16)))

    assert report.trade_count == 3
    assert report.total_pnl == pytest.approx(60.0)
    assert report.win_rate == pytest.approx(2 / 3)
    assert report.ai_comment

    # 새 세션으로 조회 — 실제로 저장됐는지 검증.
    with factory() as session:
        rows = session.query(DailyReport).all()
        assert len(rows) == 1
        assert rows[0].trade_count == 3
        assert rows[0].total_pnl == pytest.approx(60.0)
        assert rows[0].date == date(2026, 6, 16)


def test_generate_daily_empty_fills_safe():
    factory = make_session_factory("sqlite:///:memory:")
    agent = ReporterAgent(AgentRegistry(), factory, MockCommentProvider())
    report = asyncio.run(agent.generate_daily([], report_date=date(2026, 6, 16)))
    assert report.trade_count == 0
    assert report.total_pnl == pytest.approx(0.0)
    assert report.win_rate == pytest.approx(0.0)


# --- ClaudeCommentProvider 골격 ---


def test_claude_comment_provider_without_key_raises():
    provider = ClaudeCommentProvider(api_key=None)
    with pytest.raises(ValueError):
        asyncio.run(provider.comment(DailyStats(0.0, 0.0, 0)))
