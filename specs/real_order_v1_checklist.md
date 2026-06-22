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
