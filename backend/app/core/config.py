"""애플리케이션 설정.

시크릿(Claude API 키)은 `.env`에서만 읽는다. 하드코딩 금지.
Robinhood는 공개 API 키가 없다 — robinhood-trading MCP 서버로 인증/조회/주문한다.
`robinhood_mcp_enabled`로 실거래 provider를 토글한다(기본 False → Mock, 실거래 없음).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 / `.env` 기반 설정.

    시크릿은 기본값을 `None`으로 두고 `.env`에서 주입한다.
    """

    # Robinhood MCP provider 토글. 안전 기본값 False → MockProvider(실거래/실조회 없음).
    robinhood_mcp_enabled: bool = False
    claude_api_key: str | None = None
    database_url: str = "sqlite:///./trading_bot.db"
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
