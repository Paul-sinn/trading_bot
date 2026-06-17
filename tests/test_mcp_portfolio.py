"""Step 4 mcp_portfolio 테스트 (TDD Red→Green).

spec: specs/mcp_portfolio.md
- MockPortfolioProvider.get_portfolio() → spec대로 Portfolio 반환.
- Portfolio.day_pnl/필드 검증, 빈 포지션 케이스, total_equity 일관성.
- GET /api/portfolio → 200 + 스키마 일치 (mock provider 주입).
- RobinhoodPortfolioProvider는 키 없이 호출 시 명확한 예외.
- get_portfolio_provider()는 키 유무로 안전하게 분기.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import app
from backend.app.services.portfolio import (
    MockPortfolioProvider,
    Portfolio,
    Position,
    RobinhoodPortfolioProvider,
    get_portfolio_provider,
)

client = TestClient(app)


def test_mock_provider_returns_portfolio():
    portfolio = asyncio.run(MockPortfolioProvider().get_portfolio())

    assert isinstance(portfolio, Portfolio)
    assert isinstance(portfolio.total_equity, float)
    assert isinstance(portfolio.cash, float)
    assert isinstance(portfolio.positions, list)
    assert isinstance(portfolio.day_pnl, float)
    # mock은 최소 1개 포지션을 가진다.
    assert len(portfolio.positions) >= 1
    for pos in portfolio.positions:
        assert isinstance(pos, Position)


def test_mock_provider_is_deterministic():
    a = asyncio.run(MockPortfolioProvider().get_portfolio())
    b = asyncio.run(MockPortfolioProvider().get_portfolio())
    assert a == b


def test_total_equity_consistency():
    portfolio = asyncio.run(MockPortfolioProvider().get_portfolio())
    positions_value = sum(p.quantity * p.current_price for p in portfolio.positions)
    assert portfolio.total_equity == pytest.approx(portfolio.cash + positions_value)


def test_empty_positions_allowed():
    portfolio = Portfolio(total_equity=1000.0, cash=1000.0, positions=[], day_pnl=0.0)
    assert portfolio.positions == []
    assert portfolio.total_equity == portfolio.cash


def test_negative_day_pnl_allowed():
    portfolio = Portfolio(
        total_equity=900.0, cash=900.0, positions=[], day_pnl=-100.0
    )
    assert portfolio.day_pnl == -100.0


def test_position_requires_current_price():
    with pytest.raises(Exception):
        Position(symbol="AAPL", quantity=1.0, avg_buy_price=190.0)


def test_get_portfolio_provider_mcp_disabled_uses_mock():
    # 기본(MCP 비활성) → Mock(안전 기본값, 실조회 없음).
    settings = Settings(robinhood_mcp_enabled=False)
    provider = get_portfolio_provider(settings)
    assert isinstance(provider, MockPortfolioProvider)


def test_get_portfolio_provider_mcp_enabled_uses_robinhood():
    # MCP 활성 → Robinhood MCP provider(골격).
    settings = Settings(robinhood_mcp_enabled=True)
    provider = get_portfolio_provider(settings)
    assert isinstance(provider, RobinhoodPortfolioProvider)


def test_robinhood_provider_raises_without_real_integration():
    with pytest.raises(NotImplementedError):
        asyncio.run(RobinhoodPortfolioProvider().get_portfolio())


def test_get_portfolio_endpoint_returns_schema():
    resp = client.get("/api/portfolio")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"total_equity", "cash", "positions", "day_pnl"}
    assert isinstance(body["positions"], list)
    for pos in body["positions"]:
        assert set(pos.keys()) == {
            "symbol",
            "quantity",
            "avg_buy_price",
            "current_price",
        }
