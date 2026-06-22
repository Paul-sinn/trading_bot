# SPEC: Live Trading Session control

> 대시보드의 Start / Stop / Emergency-Halt 흐름을 **구조적으로** 가능하게 한다.
> `UI 버튼 → FastAPI(/api/live/*) → LiveSessionManager → (실행 경로) → Robinhood MCP 어댑터`.
> **이 기능은 실주문을 내지 않는다.** Robinhood MCP는 아직 미연동(placeholder)이며, 없으면
> `NOT_READY_NO_MCP`를 반환할 뿐 크래시하지 않는다.

## 범위 / 불변식 (CRITICAL)

- **실주문 없음**: `real_orders_placed`는 항상 0. 브로커 주문 경로 미연결. placeholder 어댑터는
  모든 브로커 메서드에서 명확한 예외를 던진다(성공을 위조하지 않음).
- **Shadow Report와 분리**: Live는 Shadow를 필요로 하지 않고, shadow 파일(`reports/*shadow*`,
  `signal_decision_log.jsonl` 등)에 쓰지 않는다. Live 산출물은 `reports/live_*.jsonl` 전용.
- **Norgate 불요**: live start는 Norgate 데이터를 요구하지 않는다(추후 명시 설정 시에만).
- **변경 금지**: 잠긴 베이스라인 기본값, 기본 유니버스, scanner/decision/sizing/RiskGate 로직,
  Shadow 동작, Norgate 리서치 파이프라인.
- **fail-safe 재시작**: 세션 상태는 in-memory. 백엔드 재시작 시 `automation_running`은 false로
  리셋(재시작이 자동매매를 절대 재개하지 않음).

## 상태 모델 — `LiveSessionState`

| 필드 | 타입 | 의미 |
|------|------|------|
| `automation_running` | bool | 자동화 루프 가동 여부(주문 허용의 필요조건) |
| `trading_mode` | `report_only`\|`live_auto` | 기본 `report_only` |
| `session_id` | str\|null | 현재/마지막 세션 uuid4 |
| `started_at` | ISO str\|null | |
| `stopped_at` | ISO str\|null | |
| `stop_reason` | str\|null | |
| `emergency_halt` | bool | true면 신규 주문 영구 차단(해제 전까지) |
| `live_enabled` | bool | `LIVE_TRADING_ENABLED` 반영 |
| `broker_connected` | bool | 어댑터 `check_availability()` 결과(읽기 전용) |
| `last_heartbeat` | ISO str\|null | status 조회 시 갱신 |
| `real_orders_placed` | int | **항상 0** |
| `daily_order_count` | int | 오늘 제출 주문 수(현재 0) |
| `current_exposure` | float | 포지션 노출(현재 0.0, 어댑터 없음) |

## 동작

### `start(mode)` — preflight 순서 (하나라도 실패 시 `automation_running` 불변)
1. `live_auto`인데 `live_enabled=false` → `BLOCKED_LIVE_DISABLED`
2. `emergency_halt=true` → `BLOCKED_EMERGENCY_HALT`
3. 알 수 없는 mode → `BLOCKED_INVALID_MODE`
4. 어댑터 `check_availability()` False → `NOT_READY_NO_MCP` (크래시 없음)
5. 어댑터 있으면 `get_account_status`/`get_buying_power`/`get_positions` 프로브
6. 통과 시: `session_id`=uuid4, `automation_running=True`, `started_at` 설정,
   `live_sessions.jsonl`에 start 이벤트 append. **report_only 성공도 자동화는 켜지되 실주문 경로 없음.**

### `stop(reason)`
- 즉시 `automation_running=False` (신규 주문 즉시 차단), `stop_reason`/`stopped_at` 기록.
- 어댑터 사용 가능 시에만 `cancel_open_orders()` 시도(`RobinhoodMcpNotConfigured` 흡수).
- stop 이벤트 append → 일간 기록 upsert. **포지션 자동청산 안 함**(별도 청산 엔드포인트는 추후).

### `emergency_halt()`
- `emergency_halt=True` + `automation_running=False`, 신규 주문 차단,
  어댑터 있으면 `cancel_open_orders()` 시도, emergency 이벤트 append.

### `can_place_new_order()` — 중앙 초크포인트
- `automation_running and not emergency_halt`. stop/halt가 즉시 신규 주문을 막는 단일 지점.

### `status()` — **읽기 전용**(UI 새로고침이 매매를 시작하지 않음)
- 상태를 변형하지 않는다. `broker_connected`/`last_heartbeat`만 비파괴적으로 갱신.

## Robinhood MCP 어댑터 경계 — placeholder

`check_availability()`(→False)/`connect()`/`get_account_status()`/`get_buying_power()`/
`get_positions()`/`get_open_orders()`/`cancel_open_orders()`/`place_limit_buy()`/`get_order_status()`.
placeholder는 `check_availability`→False, 그 외 전부 `RobinhoodMcpNotConfigured("Robinhood MCP not configured")`.
**브로커 호출 성공 위조 금지. 실주문 금지.**

## 일간 기록 — `reports/live_daily_records.jsonl` (date 멱등 upsert)
`date, session_ids, started_at, stopped_at, orders_submitted, orders_filled, orders_cancelled,
realized_pnl, unrealized_pnl, win_rate, max_drawdown_intraday, stop_reason, notes`.
MCP 없으면 주문/pnl=0/None, notes에 "no broker connected". **기록 생성은 절대 주문하지 않음.**

## 주간 기록 — 일간에서 집계(순수 함수)
`week_start, week_end, trading_days, total_orders, filled_orders, realized_pnl, win_rate,
max_daily_loss, notes`. 일간 jsonl만 읽음.

## API
- `GET /api/live/status` → state (읽기 전용)
- `POST /api/live/start` {mode?} → {status, state} (MCP 없음 ⇒ 200 + `NOT_READY_NO_MCP`)
- `POST /api/live/stop` {reason?} → {status, state}
- `POST /api/live/emergency-halt` → {status, state}
- `GET /api/live/daily-record?date=` (YYYY-MM-DD 검증)
- `GET /api/live/weekly-record` → weeks[]
- 모든 응답에 `real_orders_placed: 0`.

## 엣지케이스
- reports/ 디렉토리 부재 → 쓰기 전 생성.
- jsonl 손상/부재 → 빈 상태로 안전 처리(크래시 없음).
- 잘못된 date 형식 → 거부(주문 경로 없음).
- 어댑터 예외(`RobinhoodMcpNotConfigured`) → stop/halt 경로에서 흡수(상태 전이는 진행).
