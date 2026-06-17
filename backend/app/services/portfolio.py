"""포트폴리오 fetch 서비스 — Robinhood MCP 추상화 레이어.

CRITICAL: 외부 API(Robinhood MCP) 호출은 이 service 레이어에만 격리한다 (ADR-001).
키가 없으면 안전 기본값으로 `MockPortfolioProvider`를 쓰고, 실거래/실조회를
시도하지 않는다. 실제 MCP 연동/인증은 후속 phase 범위다.

spec: specs/mcp_portfolio.md
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from backend.app.core.config import Settings


class Position(BaseModel):
    """단일 보유 포지션."""

    symbol: str
    quantity: float
    avg_buy_price: float
    current_price: float


class Portfolio(BaseModel):
    """계좌 포트폴리오 스냅샷."""

    total_equity: float
    cash: float
    positions: list[Position]
    day_pnl: float


@runtime_checkable
class PortfolioProvider(Protocol):
    """포트폴리오 조회 인터페이스. 구현은 Mock/Robinhood로 분기."""

    async def get_portfolio(self) -> Portfolio: ...


# Mock 결정론적 고정 데이터. 외부 의존성/난수 없음.
_MOCK_CASH: float = 5000.0
_MOCK_POSITIONS: list[Position] = [
    Position(symbol="AAPL", quantity=10.0, avg_buy_price=190.0, current_price=195.0),
    Position(symbol="TSLA", quantity=5.0, avg_buy_price=250.0, current_price=240.0),
]
_MOCK_DAY_PNL: float = -50.0


class MockPortfolioProvider:
    """결정론적 고정 데이터를 반환하는 Mock provider.

    키 부재 시 안전 기본값. 같은 호출은 항상 같은 `Portfolio`를 돌려준다.
    """

    async def get_portfolio(self) -> Portfolio:
        positions = [p.model_copy() for p in _MOCK_POSITIONS]
        positions_value = sum(p.quantity * p.current_price for p in positions)
        return Portfolio(
            total_equity=_MOCK_CASH + positions_value,
            cash=_MOCK_CASH,
            positions=positions,
            day_pnl=_MOCK_DAY_PNL,
        )


class RobinhoodPortfolioProvider:
    """Robinhood MCP 기반 포트폴리오 조회 (실연동 골격).

    Robinhood는 공개 API 키가 없다. robinhood-trading MCP 서버(get_portfolio /
    get_equity_positions 등)로 조회한다. 이 단계에서는 MCP 브리지를 채우지 않고
    명확한 예외를 던져 실조회로 새지 않게 한다(안전 최우선). 통합 phase에서 구현한다.
    """

    def __init__(self, mcp_client: object | None = None) -> None:
        self._mcp = mcp_client

    async def get_portfolio(self) -> Portfolio:
        # 통합 phase 실연동 시(주석):
        #   acct = await self._mcp.get_portfolio()
        #   positions = await self._mcp.get_equity_positions()
        #   return Portfolio(...)  # MCP 응답 → Portfolio 변환
        raise NotImplementedError(
            "Robinhood MCP 연동은 통합 phase에서 구현한다. "
            "현재는 robinhood-trading MCP 조회를 시도하지 않는다."
        )


def get_portfolio_provider(settings: Settings | None = None) -> PortfolioProvider:
    """설정 기반 provider 선택.

    `robinhood_mcp_enabled`가 False(기본)면 Mock(안전 기본값, 실조회 없음),
    True면 Robinhood MCP provider(골격)을 돌려준다.
    """
    settings = settings or Settings()
    if settings.robinhood_mcp_enabled:
        return RobinhoodPortfolioProvider()
    return MockPortfolioProvider()
