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

    # 라이브 시장데이터 provider. 허용 값: "mock"(기본, 결정론·네트워크 없음) / "free"(yfinance 무료) /
    # "alpaca"(Alpaca 시장데이터 — 시세 전용, 거래 아님). 알 수 없는 값은 팩토리에서 fail-closed(예외).
    market_data_provider: str = "mock"

    # Alpaca 시장데이터(시세 전용 — 주문/거래에 절대 사용 안 함). 키는 .env에서만. 키 없으면 fail-safe
    # (provider unavailable → 스캔이 후보를 만들지 않음). Robinhood MCP가 여전히 브로커/주문 경로다.
    alpaca_api_key_id: str | None = None
    alpaca_api_secret_key: str | None = None
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"  # Basic 플랜 무료 피드.
    alpaca_bar_timeframe: str = "1Day"
    alpaca_lookback_days: int = 300
    alpaca_trading_enabled: bool = False  # 안전: Alpaca는 시장데이터 전용, 거래 비활성.
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
    max_notional_per_real_order_usd: float = 100.0  # 실주문 1건 최대 노셔널(감독 거래 상한 $100).
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
    # 전략/라이브스캔 생성 intent만 실주문 허용(테스트성/수동 조작 intent 기본 차단).
    strategy_intent_only_for_real_order: bool = True
    # 테스트 전용 intent로 실주문 내는 것을 허용할지(기본 금지 — fail-closed).
    test_only_intent_real_order_allowed: bool = False

    # 자동 주문 라우터(v1) — 전략 생성 BUY 후보 중 1개를 자동 선택해 $100 이하 실주문 프리뷰를 만들고
    # Discord 승인 요청을 보낸다. **주문 제출은 하지 않는다**(승인 게이트 뒤에서만, 별도 단계). Paul이
    # 종목/지정가를 수동 선택하지 않게 한다. 모든 안전 게이트(승인·캡·시장시간·신선도)는 그대로 적용.
    order_router_max_notional_usd: float = 100.0
    order_router_allow_fractional_market_buy: bool = True  # 고가주는 달러 기반 분수 시장가 매수 프리뷰 허용.
    order_router_max_spread_pct: float = 0.003  # 호가 스프레드 상한(초과 시 후보 제외).
    order_router_quote_max_age_seconds: int = 30  # 호가 신선도 상한(초과 시 stale로 제외).
    order_router_daily_max_approval_requests: int = 1  # 하루 라우터 승인 요청 최대 건수.
    order_router_limit_buffer_pct: float = 0.001  # 지정가 = ask * (1+buffer), 안전 상한 내에서.
    order_router_min_confidence_for_fractional: float = 0.7  # 분수 시장가 매수 최소 신뢰도(고신뢰만).

    # 장중 오케스트레이터(v1) — 정규장에 스냅샷 신선도 확인 → report_only 스캔 → 라우터 → Discord 승인
    # 요청까지 자동 수행. **주문을 제출하지 않는다**(승인 요청만). 모든 안전 게이트 그대로 적용.
    orchestrator_enabled: bool = False  # 마스터 스위치(기본 OFF). start API/CLI로만 켠다.
    orchestrator_interval_seconds: int = 300  # 백그라운드 루프 주기.
    orchestrator_market_hours_only: bool = True  # 정규장 외 skip.
    orchestrator_max_approvals_per_day: int = 1  # 하루 오케스트레이터 승인 요청 상한.
    orchestrator_require_discord_approval_worker: bool = True  # Discord 봇 env 미설정 시 승인요청 생성 차단.
    orchestrator_require_fresh_broker_snapshot: bool = True  # stale 스냅샷이면 skip.

    # Discord 승인 게이트 — 실주문(매수/매도) 전 Discord에서 사람이 명시적으로 !approve 해야 한다.
    # 승인은 리스크 게이트를 우회하지 않는다(승인 + 모든 게이트 + 확인까지 통과해야 제출 시도).
    require_discord_approval_for_real_order: bool = True
    approval_request_ttl_seconds: int = 300  # 승인 요청 유효시간(만료 시 승인 불가).
    # Discord 봇 워커(승인 처리) 시크릿/설정 — .env에서만. 봇은 Robinhood를 절대 호출하지 않는다.
    discord_bot_token: str | None = None
    discord_approval_channel_id: str | None = None
    discord_allowed_user_ids: str = ""  # 콤마 구분 허용 사용자 ID(이 목록만 승인/거부 가능).

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
