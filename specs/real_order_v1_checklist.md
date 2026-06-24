# Real Order v1 — Preflight Checklist (첫 실주문 전 필수 조건)

> 이 문서는 **첫 실 Robinhood 주문**을 연결·실행하기 전 반드시 충족해야 하는 조건을 못 박는다.
> 현재 코드 상태: 실주문 경로는 **결선돼 있지 않다**(`RealRobinhoodOrderExecutor`는 항상
> `RealExecutionDisabled`). 아래 조건이 전부 충족되고 **사용자의 명시적 승인**이 있기 전에는 실주문이
> 발생하지 않는다. 헌장 §3/§10(검증·greenlight 전 라이브 금지)에 종속된다.

## 0. 대전제
- **첫 실주문은 사용자의 명시적 승인을 반드시 요구한다.** 봇/에이전트가 자동으로 첫 주문을 내지 않는다.
- 실주문 경로(MCP write)는 별도 phase에서, ExecutionGate + 수동 arm 뒤에만 결선한다.
- test/proof(모의 시장시간) 영수증은 **프로덕션 준비도로 절대 간주하지 않는다**(environment 분리).

## 1. 설정 게이트 (모두 충족)
| config | 필수값 | 의미 |
|---|---|---|
| `ENABLE_REAL_ORDER_EXECUTION` | true (명시적) | 마스터 스위치. 기본 false. |
| `REQUIRE_MANUAL_ARM` | true | 수동 arm 없이는 실행 불가. |
| `MAX_NOTIONAL_PER_REAL_ORDER_USD` | ≤ 25 | 1건 최대 노셔널(소액). |
| `MAX_REAL_ORDERS_PER_DAY` | 1 | 하루 1건. |
| `ALLOW_REAL_SELL_ORDERS` | false | 매도 자동화 금지(매수만). |
| `ALLOW_OPTIONS_TRADING` | false | 옵션 금지(주식만). |
| `REQUIRE_MARKET_HOURS_FOR_REAL_ORDER` | true | 정규장에서만. |
| `REQUIRE_FRESH_BROKER_SNAPSHOT_FOR_REAL_ORDER` | true | stale 스냅샷 금지. |
| `AGENTIC_ACCOUNT_ONLY` | true | agentic_allowed 계정에서만. |

## 2. 수동 arm 요구사항 (`reports/real_order_arm.json`)
- `armed=true`, `expires_at`가 미래(짧은 TTL, 기본 120초).
- `max_notional` ≤ 25.
- `allowed_symbol`이 대상 intent 심볼과 일치(선택이지만 권장).
- 파일이 없거나/만료/손상/`armed=false`면 → **REAL_BLOCKED**.

## 3. 주문 자체 제약
- **Agentic 계정 전용**: 워커는 `agentic_allowed=true` 계정만 스냅샷/대상으로 한다. 계정 미상이면 차단.
- **limit BUY만**: `side=BUY`, `planned_order_type=limit`. 시장가·매도·옵션 금지.
- **최대 $25**: `notional ≤ MAX_NOTIONAL_PER_REAL_ORDER_USD`.
- **하루 1건**: `daily_real_count < MAX_REAL_ORDERS_PER_DAY`.
- **정규 시장시간**: 실 시장시간(모의 아님)에만. 모의 시장시간 통과는 test/proof로만 기록.
- **신선한 broker 스냅샷**: stale 아님(`broker_snapshot_max_age_seconds` 이내).
- **중복 미체결 매수 없음**: 같은 심볼 open buy가 있으면 차단.
- **멱등**: 같은 idempotency_key가 이미 실행되었으면 차단.

## 4. 금지 (v1에서 절대 안 함)
- 옵션 거래 / 매도 자동화 / 주문 취소 / 주문 review API 호출.
- ExecutionGate·수동 arm·시장시간·스냅샷 게이트 우회.

## 5. Stop 버튼 동작
- `STOP` → `control_flags.json`: `automation_running=false`, `block_new_orders=true`,
  `block_new_llm_calls=true`. 워커는 어떤 액션 전에도 이 플래그를 먼저 확인(fail-closed).
- `EMERGENCY_HALT` → 위에 더해 `emergency_halt=true`. 즉시 신규 주문/실행 차단.
- 포지션 자동청산은 하지 않는다(청산은 dry-run exit manager가 신호만 생성).

## 6. Rollback / Kill-switch
1. 대시보드 또는 API로 **STOP / EMERGENCY_HALT** 호출 → `block_new_orders=true`.
2. `ENABLE_REAL_ORDER_EXECUTION=false`로 되돌린다(마스터 스위치 OFF).
3. `reports/real_order_arm.json` 삭제(arm 해제).
4. 필요 시 `claude mcp remove "robinhood-trading" -s local`로 MCP 자체를 분리.
5. 확인: `GET /api/live/execution-status` → `real_execution_enabled=false`, `arm_status` ∈ {missing, disarmed, expired}.

## 7. 프로덕션 준비도 판정 (혼동 방지)
- `GET /api/live/execution-status`의 `latest_decision`은 **environment=production·실 시장시간** 영수증만 반영.
- 모의 시장시간 proof(`environment=test`, `is_proof_run=true`)는 `test_proof_count`로만 노출 — 절대
  프로덕션 준비 신호가 아니다. 대시보드 라벨: "Production readiness only uses real market-hours receipts."

## 8. 최종 확인
- [ ] 위 1–3 전부 충족 + 실 시장시간 production 영수증으로 `REAL_READY_DRY_RUN` 확인.
- [ ] **사용자 명시적 승인** 획득(첫 실주문 한정).
- [ ] 실 MCP write 결선 PR은 별도 리뷰 + 이 체크리스트 재확인.
- 현재까지: `real_orders_placed=0`, `real_order_placed=false`, `broker_order_id=null` 유지.

## 9. 실 매도 제출 결선 v1 (confirm-gated, 기본 비활성)
`backend/app/services/real_sell_executor.py`. 매수와 동형의 게이트 체인에 **확인 문구 게이트**를 추가하고
미래 실 매도 경로를 결선한다. 단, 실 executor(`RealRobinhoodSellExecutor`)는 **항상 disabled**라
프로덕션 제출은 fail-closed(SELL_BLOCKED). 테스트만 `MockSellExecutor`로 `SELL_SUBMITTED`(environment=test).

- **인터페이스**: `submit_limit_sell(symbol, quantity, limit_price, account_id=None)`.
- **실 제출 요건(전부 충족 시에만 executor 호출)**: `ALLOW_REAL_SELL_ORDERS=true` · 유효 `real_sell_arm.json`
  (armed·미만료·allowed_symbol·max_quantity·min_limit_price) · fresh broker snapshot · 포지션 존재 ·
  `quantity ≤ shares_available_for_sells` · **지정가 매도만** · equity만(옵션 금지) · 정규장만 ·
  중복 미체결 매도 없음 · 멱등 미사용 · control_flags 허용 · **정확한 확인 문구 `CONFIRM_REAL_SELL_1`**.
- **확인 계약(`build_sell_preview`)**: 실 제출 직전 SYMBOL·SIDE=SELL·TYPE=LIMIT·QUANTITY·LIMIT_PRICE·
  ESTIMATED_NOTIONAL·ACCOUNT(Agentic, masked last4)·CURRENT_POSITION_QTY·SHARES_AVAILABLE_FOR_SELLS·
  MARKET_HOURS=regular only 프리뷰를 출력하고 **멈춘다**. 정확한 문구가 있어야만 제출 경로 호출.
- **영수증 분리**: 실 매도 흔적(`real_sell_order_placed=true`,`real_sell_orders_placed=1`)은
  **environment=production·real 시장시간·non-proof `SELL_SUBMITTED`**에만 보존. mock/test/proof는
  `SELL_SUBMITTED`라도 0/false 강제. `latest_production_sell_receipt`는 mocked proof를 무시.
- **금지**: 실 MCP write 호출(`mcp__robinhood…`)·매수·취소·review·옵션·공매도·live_auto.
- 현재까지: 프로덕션 `real_sell_orders_placed=0`, `real_sell_order_placed=false`, `broker_order_id=null` 유지.

## 10. Discord 승인 게이트 + 감독 거래 하드리밋
실주문(매수/매도) 전 **Discord에서 사람이 명시적으로 승인**해야 한다. 승인은 리스크 게이트를
**우회하지 않는다** — 승인 + 모든 readiness 게이트 + (매도) 확인 문구까지 통과해야 제출을 시도하고,
실 executor는 여전히 fail-closed다(이 단계에서 실주문 0).

### 하드리밋 기본값(감독 거래)
`MAX_NOTIONAL_PER_REAL_ORDER_USD=100` · `MAX_REAL_ORDERS_PER_DAY=1` ·
`REQUIRE_DISCORD_APPROVAL_FOR_REAL_ORDER=true` · `REQUIRE_MANUAL_ARM=true` · `AGENTIC_ACCOUNT_ONLY=true` ·
`REQUIRE_FRESH_BROKER_SNAPSHOT_FOR_REAL_ORDER=true` · `REQUIRE_MARKET_HOURS_FOR_REAL_ORDER=true` ·
`ALLOW_OPTIONS_TRADING=false` · `ALLOW_REAL_SELL_ORDERS=false`(매도는 §9 confirm scaffold) ·
`STRATEGY_INTENT_ONLY_FOR_REAL_ORDER=true` · `TEST_ONLY_INTENT_REAL_ORDER_ALLOWED=false`.

### 저장(append-only, gitignore)
- `reports/approval_requests.jsonl` — READY 도달 시 생성. 필드: approval_id·created_at·expires_at·type(BUY/SELL)·
  symbol·side·order_type·quantity/dollar_amount·limit_price·notional·account_last4·source_intent_id·
  strategy_id·idempotency_key·preview_hash·status·reason·broker_order_id(=null).
- `reports/approval_decisions.jsonl` — 봇 워커가 기록. 필드: approval_id·decided_at·decision(APPROVE/REJECT)·
  discord_user_id·discord_username·channel_id·message_id·raw_command·valid·reason. **시크릿/전체 계좌번호/토큰 미포함.**

### Discord 봇 워커 (`scripts/discord_approval_worker.py`)
- env: `DISCORD_BOT_TOKEN`·`DISCORD_APPROVAL_CHANNEL_ID`·`DISCORD_ALLOWED_USER_IDS`(콤마 구분).
- 명령: `!approve <approval_id>` · `!reject <approval_id>` · `!status <approval_id>`.
- 규칙: 허용 사용자만 승인/거부(목록 밖/빈 목록은 fail-closed) · 만료 요청 승인 불가 · 중복 결정 거부 ·
  알 수 없는 id 거부 · `!status`는 조회만(결정 미기록). **워커는 Robinhood를 호출하지 않고 주문을 내지 않는다 —
  approval_decisions.jsonl만 쓴다.** 명령 로직은 `services/discord_approval.process_approval_command`(테스트 가능).

### 실행 통합(`evaluate_approval_gate` / `approval_gate_for_intent`)
실주문 전 추가 전제조건: 유효 approval_id(intent로 조회) · 허용 사용자 APPROVE · 미만료 ·
`preview_hash`가 현재 주문과 일치(승인 후 변경 차단) · idempotency 미소비 · 전략/라이브스캔 intent(테스트성 기본 차단) ·
일일 실주문 < 1 · notional ≤ 100. `process_execution`(매수)·`process_sell_submit`(매도)에 병합되어
미승인이면 `REAL_BLOCKED`/`SELL_BLOCKED`. 승인은 "다음 단계(READY_DRY_RUN)"까지만 허용 — 자동 제출 없음.

### API/대시보드
- `GET /api/live/approvals?limit=50` · `/api/live/approvals/latest` · `/api/live/approvals/{approval_id}` —
  로컬 jsonl만 읽음(Discord/Robinhood 미호출).
- 대시보드 "Discord 승인 게이트" 패널: pending 승인·최근 결정·만료 상태·approve/reject 명령 예시.
  라벨: "Discord approval is required before any real order. Approval does not bypass risk gates."
- 현재까지: `real_orders_placed=0`, `real_order_placed=false`, `broker_order_id=null` 유지. live_auto는 감독/수동 게이트만.

## 11. 자동 주문 라우터 v1 (감독 거래 — 봇이 종목/지정가 선택)
`backend/app/services/order_router.py`. accepted_dry_run BUY OrderIntent들 중 1개를 자동 선택해 $100 이하
실주문 프리뷰를 만들고 §10 승인 요청을 생성한다. **주문 제출은 없다**(승인 게이트 뒤 별도 단계). Paul이
종목/지정가를 수동 선택하지 않는다.

- **후보 자격**: 전략/라이브스캔 생성(strategy_id==live_strategy_id) · 테스트성 차단 · BUY only · equity only ·
  LLM/mock approve · accepted_dry_run · 미실행(executed_keys) · 중복 승인요청 없음 · 중복 미체결 매수 없음.
- **글로벌 차단**: 스냅샷 없음/stale · 장시간 아님 · `MAX_REAL_ORDERS_PER_DAY` 초과 ·
  `ORDER_ROUTER_DAILY_MAX_APPROVAL_REQUESTS` 초과 → ROUTER_BLOCKED.
- **호가 게이트**: 호가 없음(missing) · stale(`ORDER_ROUTER_QUOTE_MAX_AGE_SECONDS`) · 와이드 스프레드
  (`ORDER_ROUTER_MAX_SPREAD_PCT`) → 후보 제외.
- **결정론적 랭킹**: 높은 신뢰도 + 좁은 스프레드 + 신선 호가 선호. 동점은 symbol 알파벳.
- **주문유형 정책($100 캡)**: ref(ask 우선)≤$100 → 지정가 매수(limit=ask*(1+buffer), 캡 내, qty=floor(100/limit)≥1).
  ref>$100 → 분수 시장가 매수(dollar_amount=$100), `ORDER_ROUTER_ALLOW_FRACTIONAL_MARKET_BUY=true` +
  최소 신뢰도(`ORDER_ROUTER_MIN_CONFIDENCE_FOR_FRACTIONAL`)일 때만. 비활성/저신뢰면 차단.
- **승인 요청**: 선택 시 §10 `create_approval_request`로 PENDING 요청 생성 + Discord 전송. bid/ask/last/spread%
  포함, preview_hash 계산(주문 변경 시 해시 변경 → 게이트 차단). 결정은 `order_router_decisions.jsonl`에 기록.
- **API/대시보드**: `GET /api/live/order-router/status`·`/order-router/latest`(읽기 전용 — 선택/승인 실행 안 함).
  "자동 주문 라우터" 패널: 선택 후보·주문유형·노셔널·지정가/달러·스프레드·라우터 결정·승인 상태.
  라벨: "Bot selects the trade. Discord approval is still required before any real order."
- **금지**: 주문 제출·Robinhood write(`mcp__robinhood…`)·`place_equity_order`·옵션·매도·unsupervised auto.
- 현재까지: `real_orders_placed=0` 항상. 라우터는 후보 선택 + 승인 요청까지만 — 실주문 0.

## 12. 장중 오케스트레이터 v1 (감독 자동매매 — 승인 요청만)
`backend/app/services/market_hours_orchestrator.py`. 정규장에 자동으로:
스냅샷 신선도 확인 → report_only 라이브 스캔 1회 → 자동 주문 라우터(§11) → (선택 시) Discord 승인 요청(§10).
**실주문을 직접 내지 않는다.**

- **run_once 게이트 순서**: 장시간(`ORCHESTRATOR_MARKET_HOURS_ONLY`) → 스냅샷 신선도
  (`ORCHESTRATOR_REQUIRE_FRESH_BROKER_SNAPSHOT`) → 일일 실주문 캡(`MAX_REAL_ORDERS_PER_DAY`) →
  일일 승인 캡(`ORCHESTRATOR_MAX_APPROVALS_PER_DAY`) → 대기 승인 존재 → Discord 봇 env 준비
  (`ORCHESTRATOR_REQUIRE_DISCORD_APPROVAL_WORKER`) → 스캔 1회 → 라우터 → 승인 요청. 위반 시 안전 skip/warn.
- **이벤트 로그**: `reports/orchestrator_events.jsonl` (timestamp·event_type·market_open·action·result·reason·
  router_decision·approval_id?·real_orders_placed=0·errors). `action` ∈ {skip, run, approval_requested,
  router_blocked, warn}.
- **API**(읽기/제어): `GET /api/live/orchestrator/status`·`/orchestrator/events?limit=` ·
  `POST /orchestrator/run-once`·`/orchestrator/start`·`/orchestrator/stop`. run-once/start는 스캔/라우터/승인요청만 —
  **주문 제출 없음, Robinhood write 미호출**. start는 안전 interval 백그라운드 루프, stop은 루프 정지.
- **CLI**: `scripts/run_market_orchestrator.py --once|--loop` (시크릿 미출력, 브로커 write 없음).
- **대시보드**: "Market Orchestrator" 패널 — enabled/running·시장 open/closed·마지막 실행·최근 라우터 결정·
  대기 승인 id·차단 사유·오늘 승인/실주문 카운트. 라벨: "Orchestrator only creates Discord approval requests.
  It never submits orders."
- **Config**: `ORCHESTRATOR_ENABLED=false`·`ORCHESTRATOR_INTERVAL_SECONDS=300`·`ORCHESTRATOR_MARKET_HOURS_ONLY=true`·
  `ORCHESTRATOR_MAX_APPROVALS_PER_DAY=1`·`ORCHESTRATOR_REQUIRE_DISCORD_APPROVAL_WORKER=true`·
  `ORCHESTRATOR_REQUIRE_FRESH_BROKER_SNAPSHOT=true`.
- **금지**: 주문 제출·Robinhood write(`mcp__robinhood…`)·`place_equity_order`·unsupervised auto.
- 현재까지: `real_orders_placed=0` 항상. 오케스트레이터는 승인 요청까지만 — 실주문 0.

## 13. Discord 승인 실행 워커 v1 (승인 후 1건 — 게이트 전부 재확인)
`backend/app/services/approved_execution.py` + `scripts/approved_execution_worker.py`.
오케스트레이터가 만든 승인 요청을 Paul이 `!approve` 한 뒤, 이 워커가 처리한다. **승인은 게이트를
우회하지 않는다** — 제출 전 모든 리스크 게이트를 재확인한다. 구현/테스트 중 실주문 0.

- **모드**: `--dry-run`(기본 — 제출 없음, READY/BLOCKED 영수증만) · `--execute-real`(미래 라이브 전용 —
  모든 게이트 통과 시에만 1건). 실 executor는 항상 disabled → 프로덕션 `--execute-real`도 fail-closed(BLOCKED).
- **재확인 게이트**: 최신 APPROVED 결정 존재 · 매칭 요청 존재 · 만료 아님 · 허용 Discord 사용자 ·
  preview_hash 일치 · idempotency 미사용 · 전략/라이브스캔 intent(테스트성 차단) · fresh 스냅샷 ·
  정규장 · Agentic 계정 · 일일 실주문 < 1 · notional ≤ 100 · 중복 미체결 매수 없음 · BUY/limit·market only.
- **영수증**(기존 `real_execution_receipts.jsonl` 재사용): approval_id·source_intent_id·strategy_id·order_type·
  symbol·side·quantity/dollar_amount·limit_price·notional·decision(BLOCKED/APPROVED_READY_DRY_RUN/REAL_SUBMITTED/
  ERROR)·broker_order_id·real_order_placed·real_orders_placed·reason. 실 흔적(placed=true/1)은
  **production·real 시장시간·non-proof REAL_SUBMITTED**에만. mock/test는 REAL_SUBMITTED라도 0 강제.
  `daily_real_order_count`도 진짜 실 제출만 집계.
- **--execute-real 통과 시**(미래): 라우터 프리뷰 기준 BUY 1건만(limit 또는 fractional market), 재시도/2차 없음,
  REAL_SUBMITTED 기록(broker_order_id), 스냅샷 read-only 갱신, Discord 알림.
- **API/대시보드**: `/api/live/execution-status`에 latest_approval_id·latest_order_type 추가. 패널 라벨:
  "Discord approval is required, and all risk gates are rechecked before any order."
- **금지**: 실주문(이 task)·매도/취소/review·옵션·재시도·Robinhood write(`mcp__robinhood…`/`place_equity_order`).
- 현재까지: `real_orders_placed=0` 항상. dry-run은 APPROVED_READY_DRY_RUN, 실 경로는 fail-closed.

## 14. 승인 실 BUY 제출 워커 v1 (limit/fractional 제출 결선 + 멱등)
§13을 확장해 최종 BUY 제출 경로를 결선한다. `--execute-real`에서 모든 게이트가 재통과하면 **정확히 1건**만
제출한다. 실 executor는 기본 disabled(fail-closed) — 프로덕션 `--execute-real`도 BLOCKED. 테스트는 mock만.

- **제출 인터페이스**: `submit_limit_buy(symbol, quantity, limit_price)` · `submit_market_buy(symbol, dollar_amount)`
  (Real*는 둘 다 RealExecutionDisabled, Mock은 가짜 id). 라우터 프리뷰대로 분기:
  limit → 지정가 수량 매수, market → 분수 시장가 매수(dollar_amount ≤ $100).
- **멱등**: `executed_keys`가 MOCK_SUBMITTED + **REAL_SUBMITTED**를 포함 → 같은 source_intent_id 2차 제출 차단
  (재시도/2차 주문 없음).
- **실 흔적 보존**: production·real 시장시간·non-proof REAL_SUBMITTED만 `real_order_placed=true`/`real_orders_placed=1`.
  mock/test는 REAL_SUBMITTED라도 0 강제.
- **API/대시보드**: `/api/live/execution-status`에 latest_broker_order_id 추가. 패널 라벨:
  "Discord approval triggers execution only after all gates are rechecked." (승인 id·주문유형·broker_order_id 표시)
- **금지**: 실주문(이 task)·매도/취소/review·옵션·재시도·2차 주문·Robinhood write(`mcp__robinhood…`/`place_equity_order`).

## 15. 승인 Robinhood MCP 제출 브리지 v1 (워커 컨텍스트 전용 executor)
`RobinhoodMcpBuyExecutor`(`backend/app/services/real_order_executor.py`) — 승인 BUY를 실제 Robinhood MCP로
제출하는 **명명된 브리지**. 단, 실제 도구명은 코드에 하드코딩하지 않는다(네임스페이스 미포함).

- **사용 제약**: 워커가 런타임에 `submit_fn`을 주입하고 `worker_context=True`일 때만 콜백으로 1건 제출.
  그 외(특히 FastAPI에서 직접 생성)에는 **항상 RealExecutionDisabled**(fail-closed). FastAPI는 worker_context를
  켤 수 없다 → 백엔드에서 직접 실주문 불가.
- **지원**: limit BUY(symbol·quantity·limit_price) · 분수 market BUY(symbol·dollar_amount ≤ $100). 재시도/2차 없음.
- **승인 실행 워커 연결**: `approved_execution`의 `--execute-real` 기본 executor가 `RobinhoodMcpBuyExecutor()`
  (워커 컨텍스트 아님 → fail-closed → BLOCKED). 모든 게이트(§13/§14)는 제출 전 재확인. 테스트는 mock/주입 콜백만.
- **영수증**: `submit_mode`(dry_run|execute_real) 추가. 실 흔적(placed=true/1)은 production·real·non-proof
  REAL_SUBMITTED만. mock/test/주입-콜백(mocked 시장시간)은 0 강제.
- **API/대시보드**: execution-status에 latest_submit_mode·latest_broker_order_id. 라벨:
  "Discord-approved orders are submitted only after all gates are rechecked."
- 현재까지: `real_orders_placed=0` 항상. 실 MCP write는 워커가 submit_fn을 주입하는 미래 라이브 런에서만.

## 16. Alpaca 시장데이터 provider v1 (시세 전용 — 거래 아님)
`backend/app/services/market_data.py`의 `AlpacaMarketDataProvider`. `MARKET_DATA_PROVIDER=alpaca`면 라이브
스캔이 mock/yfinance 대신 Alpaca 시세를 쓴다. **Alpaca는 시세 전용 — 주문/거래에 절대 사용 안 함.**
Robinhood MCP가 여전히 브로커/주문 경로다. `ALPACA_TRADING_ENABLED=false`.

- **env**: `ALPACA_API_KEY_ID`·`ALPACA_API_SECRET_KEY`·`ALPACA_DATA_BASE_URL`(기본 https://data.alpaca.markets)·
  `ALPACA_DATA_FEED=iex`·`ALPACA_BAR_TIMEFRAME=1Day`·`ALPACA_LOOKBACK_DAYS=300`. 키는 헤더로만, 로그/페이로드 미노출.
- **메서드**: `get_recent_bars(symbol, lookback_days)`(정규화 OHLCV DataFrame, attrs.source/feed) ·
  `get_latest_quote(symbol)`·`get_batch_latest_quotes(symbols)` → `MarketQuote`(symbol·bid·ask·last·
  quote_timestamp·source="alpaca"·feed="iex"). 테스트는 `http_get` 주입으로 네트워크 없이 검증.
- **선택**: `get_market_data_provider`가 alpaca를 인식(`ALLOWED_PROVIDERS=mock|free|alpaca`). 상태/대시보드의
  provider 이름이 alpaca로 표시. mock은 테스트용으로 유지.
- **라우터 통합**: provider=alpaca면 라우터가 ref price/spread/freshness에 **Alpaca 라이브 호가 우선**(없으면
  브로커 스냅샷 폴백). buying_power/positions/open_orders는 여전히 Robinhood 스냅샷으로 점검. Alpaca 타임스탬프는
  나노초/Z를 정규화(`_norm_ts`).
- **fail-safe**: 키 미설정/API 오류/레이트리밋/stale/빈 bars → 예외 또는 빈 프레임 → 스캔 ERROR/INSUFFICIENT_DATA →
  **BUY_CANDIDATE 없음 → 승인 요청 생성 안 됨**. 라우터 호가 fetch 실패도 {} → 후보 차단.
- **금지**: 주문/Robinhood write(`mcp__robinhood…`/`place_equity_order`)·Alpaca 거래. 시세 전용.
