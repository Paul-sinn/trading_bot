# Spec: Broker Snapshot Worker Bridge v0 (read-only)

## 목적
FastAPI가 Robinhood MCP에 직접 못 붙으므로(최초 인가가 대화형 OAuth — `reports/fastapi_mcp_feasibility.md`),
v0에서는 **Claude/Codex가 MCP 워커**가 되어 읽기 전용 도구를 호출하고, 살균된 스냅샷을 파일로
넘긴다. backend는 파일만 읽는다. **주문/쓰기 없음, `real_orders_placed=0`.**

## 데이터 흐름
```
backend(FastAPI)                         worker(Claude/Codex + Robinhood MCP)
  ─ writes reports/control_flags.json ───▶ 액션 전 항상 확인(block_new_orders/emergency_halt)
  ◀─ reads reports/broker_snapshots.jsonl ─ 읽기 도구 호출 → 살균 → append
  GET /api/broker/snapshot  · GET /api/broker/snapshots?limit=N  → 대시보드 표시
  ExecutionGate dry-run ← 최신 스냅샷(buying_power/open_orders/staleness)
```

## 허용 MCP 도구(읽기 전용만)
`get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_orders`(state=new), `get_equity_quotes`.
write/order/review/cancel/watchlist mutation 도구는 **호출 금지**.

## 컴포넌트
- `backend/app/services/broker_snapshot.py` — `BrokerSnapshot` 스키마 + append/load/latest +
  staleness + `build_snapshot_from_raw`(원본→살균, agentic 계정 선택, 계정번호 마스킹).
- `backend/app/services/control_flags.py` — `ControlFlags`(automation_running, emergency_halt,
  block_new_orders, block_new_llm_calls, updated_at, reason) read/write. 부재/손상→None(fail-closed).
- `backend/app/api/broker.py` — `GET /api/broker/snapshot`, `GET /api/broker/snapshots`. **MCP 미호출**.
- `scripts/broker_snapshot_worker.py` — 워커 entrypoint. `--from-json`/`--from-stdin`으로 원본 JSON을
  받아 살균·적재. 입력 없으면(=MCP 미가용) **명확히 실패**(데이터 위조 금지).
- `LiveSessionManager.start/stop/emergency_halt` → `control_flags.json` 갱신.
- `ExecutionGate.evaluate(broker_snapshot=..., snapshot_max_age_seconds, reject_on_stale_snapshot)`.

## ExecutionGate 스냅샷 게이트(dry-run, 브로커 호출 없음)
- 스냅샷 없음 → 경고만(report_only 기본).
- `buying_power < planned_notional` → reject.
- 같은 심볼 미체결 매수 주문 존재 → reject(중복 방지). 매도/타심볼은 통과.
- stale 스냅샷 → 기본 경고, `reject_on_stale_snapshot=true`면 reject. 모든 경우 `real_orders_placed=0`.

## 워커 입력 JSON 형식
```json
{ "provider": "...", "source": "...",
  "accounts": <get_accounts>, "portfolio": <get_portfolio>,
  "positions": <get_equity_positions>, "open_orders": <get_equity_orders state=new>,
  "quotes": <get_equity_quotes> }
```

## 불변식 (CRITICAL)
- write/order MCP 도구 호출 0. 계정번호는 last4만(전체/토큰 미저장). `real_orders_placed=0` 강제.
- backend는 MCP 직접 호출 안 함(파일 read-only). live_auto 미관여.
- 워커는 어떤 액션 전에도 control_flags를 먼저 확인(None이면 차단으로 간주).
- `reports/*`는 .gitignore — 스냅샷/플래그는 커밋되지 않는다.

## 다음 단계(범위 밖)
- 워커 자동 스케줄(주기적 스냅샷). 주문 도구는 검증·greenlight 이후 ExecutionGate 뒤 별도 모듈에서만.
