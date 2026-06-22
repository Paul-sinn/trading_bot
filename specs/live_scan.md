# SPEC: Live MarketDataAdapter + report_only Live Quant Scan Loop

> 첫 **기능적** 라이브 스캔 레이어. report_only 모드에서 Start Trading이 quote/bar 데이터를 받아
> 베이스라인 유니버스를 스캔하고 `BUY_CANDIDATE / REJECT / SKIP / INSUFFICIENT_DATA / ERROR` 이벤트를
> 낸다. **Robinhood 없음 · 실주문 없음 · LLM 호출 없음.** 시장데이터 + 스캔 배관만.

## 불변식 (CRITICAL)
- 실주문 없음: 모든 스캔 이벤트 `real_orders_placed=0`. 주문/브로커 호출 경로 없음.
- LLM 없음: price polling·scan 어디에서도 LLM/뉴스 API 호출 없음.
- 잠긴 베이스라인 미변경: stop 0.15 / trail 0.20 / max-hold 60 / next-bar-limit, 기본 유니버스,
  scanner/decision/sizing/RiskGate 로직, Shadow 동작, Norgate 파이프라인 모두 그대로.
- Shadow와 분리: live 산출물은 `reports/live_scan_events.jsonl` 전용. shadow 파일·daily_shadow_report.md·
  Norgate에 의존하거나 쓰지 않는다. Norgate는 라이브 시작에 불필요.

## 1. MarketDataProvider 추상화 (`backend/app/services/market_data.py`)
인터페이스: `get_quote(symbol)`, `get_quotes(symbols)`, `get_recent_bars(symbol, lookback_days)`,
`provider_status()`.
- **MockMarketDataProvider**: 결정론 합성 OHLCV(SPY/VIX 포함), 네트워크 없음. 테스트용.
- **FreeMarketDataProvider**: 기존 `agents/data_adapter.FreeDailyProvider`(yfinance) 래핑.
  bars=일봉, quote=최신 종가. 네트워크/import 실패 시 graceful(provider_status.available=False,
  심볼별 실패는 ERROR 이벤트로). 유료 API·브로커·Robinhood 없음.
- Config `MARKET_DATA_PROVIDER`: 기본 `mock`, 허용 `mock|free`. **알 수 없는 값 → 팩토리에서
  `MarketDataProviderNotConfigured` (fail-closed).**

## 2. report_only 시작 (MCP 불요)
- `report_only`: emergency_halt false + 유효 시장데이터 provider(알 수 없으면 `NOT_READY_BAD_PROVIDER`).
  **Robinhood MCP 없어도 시작 가능**, `automation_running=true`(모니터링 전용). 실주문 절대 없음.
- `live_auto`: 종전대로 `LIVE_TRADING_ENABLED=true` + MCP 연동 필요, 없으면 `NOT_READY_NO_MCP`.

## 3. Live Quant Scan Loop (`backend/app/services/live_scan.py`)
- `automation_running=true`일 때만 동작. MarketDataAdapter 사용. 베이스라인 유니버스만.
  report_only로 시작. 주문/LLM 없음.
- `scan_cycle(session_id, trading_mode)`: SPY bars + VIX 1회 조회 → `classify_regime`. 각 심볼:
  - bars < 200(slow) 또는 SPY/VIX 부족 → `INSUFFICIENT_DATA`(추측 금지).
  - `algorithms.entry.pullback_entry` enter=True → `BUY_CANDIDATE`; reason "게이트 실패…" → `REJECT`;
    reason "트리거…" → `SKIP`; "데이터 부족/워밍업" → `INSUFFICIENT_DATA`.
  - provider 예외 → `ERROR`(graceful).
  features에 trend/relative_strength/rsi/regime/price 기록.
- `LIVE_SCAN_MAX_SYMBOLS_PER_CYCLE`(0=전체), `LIVE_SCAN_INTERVAL_SECONDS=300`,
  `LIVE_PRICE_POLL_INTERVAL_SECONDS=60`, `LIVE_SCAN_ENABLED=true`.
- 베이스라인 유니버스는 `LIVE_BASELINE_UNIVERSE`(experiments.BASELINE_UNIVERSE 미러). 드리프트
  가드 테스트가 동일성 보장.

## 4. 스캔 로그 (`reports/live_scan_events.jsonl`)
레코드: `timestamp, session_id, trading_mode, provider, symbol, price, scan_status, reason,
features, riskgate_status(report_only=None), buy_candidate, real_orders_placed=0`.

## 5. 세션 라이프사이클
- 성공 시작 시(LIVE_SCAN_ENABLED) **첫 cycle 동기 실행**(결정론) 후 daemon 스레드가
  interval마다 재스캔(automation_running 동안). `live_scan_running=true`.
- stop / emergency-halt: automation_running=false **즉시**, 스캔 루프·price polling 중지(stop flag +
  join), LLM·주문 없음, 포지션 청산 없음. `live_scan_running=false`.

## 6. API
- `GET /api/live/status` 확장: `market_data_provider, market_data_status, live_scan_running,
  last_scan_at, last_scan_event_count, latest_buy_candidates`.
- `GET /api/live/scan-events?limit=50`: 읽기 전용 tail. **스캔 시작 안 함·주문 안 함.** limit 1..500 clamp.

## 7. 안전 체크
- UI 새로고침/`GET status`/`GET scan-events`는 스캔을 시작하지 않는다(읽기 전용).
- provider 데이터 없음/free 네트워크 실패 → graceful(ERROR/INSUFFICIENT_DATA, 크래시 없음).
- 알 수 없는 provider → fail closed.

## 엣지케이스
- reports/ 부재 → 쓰기 전 생성. jsonl 손상/부재 → 빈 상태 안전 처리.
- SPY/VIX 부족 → 전 심볼 INSUFFICIENT_DATA(레짐 판정 불가).
- limit 비정상 → clamp.
