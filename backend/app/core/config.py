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
    # 라이브 자동매매(live_auto) 허용 마스터 스위치. 안전 기본값 False → live_auto 시작 차단.
    # report_only 시작은 이 값과 무관하게 가능하지만, 어떤 모드에서도 실주문 경로는 없다(MCP 미연동).
    live_trading_enabled: bool = False

    # 라이브 시장데이터 provider. 허용 값: "mock"(기본, 결정론·네트워크 없음) / "free"(yfinance 무료).
    # 알 수 없는 값은 팩토리에서 fail-closed(예외). Norgate는 리서치/섀도 전용 — 라이브 시작에 불필요.
    market_data_provider: str = "mock"
    # report_only 라이브 스캔 루프 토글/주기. 스캔은 주문/LLM 없이 베이스라인 유니버스만 모니터링한다.
    live_scan_enabled: bool = True
    live_price_poll_interval_seconds: int = 60
    live_scan_interval_seconds: int = 300
    live_scan_max_symbols_per_cycle: int = 0  # 0 → 베이스라인 유니버스 전체

    claude_api_key: str | None = None
    database_url: str = "sqlite:///./trading_bot.db"
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
