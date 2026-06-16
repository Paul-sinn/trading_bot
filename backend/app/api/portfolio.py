"""`/api/portfolio` 포트폴리오 조회 라우터.

provider는 의존성 주입(`get_portfolio_provider`, 기본 Mock). 외부 호출은
service 레이어에 격리한다 (ADR-001).

spec: specs/mcp_portfolio.md
"""

from fastapi import APIRouter, Depends

from backend.app.services.portfolio import (
    Portfolio,
    PortfolioProvider,
    get_portfolio_provider,
)

router = APIRouter()


@router.get("/api/portfolio", response_model=Portfolio)
async def read_portfolio(
    provider: PortfolioProvider = Depends(get_portfolio_provider),
) -> Portfolio:
    """현재 포트폴리오 스냅샷을 반환한다."""
    return await provider.get_portfolio()
