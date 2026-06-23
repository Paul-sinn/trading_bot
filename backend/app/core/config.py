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

    # Mock LLM 의사결정 파이프라인(무비용). LLM_PROVIDER 기본 mock — 실 LLM API 경로 없음(fail-closed).
    # mock 리뷰는 ai_calls_today에 카운트되지만 비용은 항상 0.00이다.
    llm_provider: str = "mock"
    max_llm_calls_per_day: int = 50
    max_llm_cost_usd_per_day: float = 5.00
    min_llm_cooldown_seconds_per_symbol: int = 900
    # ExecutionGate dry-run 한도(실주문 없음 — 계획 수치 검증용).
    max_notional_per_order_usd: float = 1000.0
    max_daily_order_intents: int = 20
    max_total_intended_exposure_usd: float = 5000.0
    live_strategy_id: str = "ts_momentum_pullback_v1"

    # Broker 스냅샷(read-only 워커 브리지). ExecutionGate dry-run이 최신 스냅샷의
    # buying_power/open_orders로 추가 검증한다. 스냅샷은 워커가 적재하며 backend는 읽기만 한다.
    broker_snapshot_max_age_seconds: int = 3600  # 이보다 오래되면 stale로 간주.
    reject_on_stale_snapshot: bool = False  # 기본: stale면 경고만(report_only). True면 reject.

    # 실주문 실행(v1 scaffold) — **모두 안전 기본값(비활성)**. 이 값들이 모두 충족돼도 현재 단계에는
    # 실 MCP 주문 경로가 결선돼 있지 않다(RealExecutionDisabled). 검증·greenlight 전 라이브 금지(헌장 §3/§10).
    enable_real_order_execution: bool = False  # 마스터 스위치(기본 OFF → 실행 차단).
    require_manual_arm: bool = True  # 실주문 전 수동 arm 파일 필수.
    real_order_arm_ttl_seconds: int = 120  # arm 유효시간(만료 시 차단).
    max_notional_per_real_order_usd: float = 25.0  # 실주문 1건 최대 노셔널(소액 상한).
    max_real_orders_per_day: int = 1  # 하루 실주문 최대 건수.
    allow_real_sell_orders: bool = False  # 매도 자동화 미허용(매수 limit만).
    allow_options_trading: bool = False  # 옵션 거래 미허용(주식만).
    require_fresh_broker_snapshot_for_real_order: bool = True  # stale 스냅샷이면 차단.
    require_market_hours_for_real_order: bool = True  # 장시간 외 차단.
    agentic_account_only: bool = True  # agentic_allowed 계정만(스냅샷 계정 미상이면 차단).
    # 실주문은 전략/라이브스캔 생성 intent(strategy_id == live_strategy_id)에서만 나가야 한다.
    # 테스트성 intent로 실주문 내는 것을 기본 차단. 첫 주문 수동 테스트는 이 플래그를 명시적으로 켤 때만
    # 허용되며, 유효 기간은 수동 arm 파일의 짧은 TTL이 사실상 제한한다(arm 만료 시 자동 차단).
    first_order_manual_test_mode: bool = False

    # Discord 알림(매매 이벤트 → webhook). 시크릿 URL은 .env에서만. 없으면 자동 비활성(no-op).
    # 카테고리별 토글로 노이즈 조절 가능(기본 전부 on). 알림은 메시지만 보내며 주문을 내지 않는다.
    discord_webhook_url: str | None = None
    discord_notify_real_orders: bool = True   # REAL_SUBMITTED / REAL_READY_DRY_RUN
    discord_notify_exits: bool = True         # 청산 신호(STOP_LOSS/TRAILING/TIME/MANUAL_CLOSE)
    discord_notify_dry_run_intents: bool = True  # WOULD_SUBMIT dry-run 주문계획
    discord_notify_blocks: bool = True        # REAL_BLOCKED / BLOCKED / ERROR

    claude_api_key: str | None = None
    database_url: str = "sqlite:///./trading_bot.db"
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
