"""애플리케이션 설정.

시크릿(Robinhood/Claude API 키)은 `.env`에서만 읽는다. 하드코딩 금지.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경 변수 / `.env` 기반 설정.

    시크릿은 기본값을 `None`으로 두고 `.env`에서 주입한다.
    """

    robinhood_api_key: str | None = None
    claude_api_key: str | None = None
    database_url: str = "sqlite:///./trading_bot.db"
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
