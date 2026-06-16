# Step 4: executor-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL: 모든 주문은 알고리즘→Claude→리스크게이트 통과)
- `/docs/PRD.md` (실행 에이전트: MCP 주문 실행, 체결 확인, 슬리피지 기록)
- `/docs/ADR.md` (ADR-003: 리스크 게이트 / ADR-001: 외부의존 backend·agent 격리)
- `/agents/base.py`, `/agents/risk.py`, `/specs/risk_agent.md` (`check_risk_gate`/`RiskAgent`)
- `/agents/decision.py`, `/specs/decision_agent.md` (`Decision`, `DecisionResult`)
- `/algorithms/sizing.py` (`position_size`, `PositionPlan` — 수량 결정)

## 작업

판단 결과(BUY/SELL)를 받아 **리스크 게이트를 통과한 뒤** 주문을 실행하고, 체결 확인·슬리피지를 기록하는 실행 에이전트를 구현한다. MCP 주문은 mock으로 추상화한다.

**SDD → TDD 순서를 강제한다. 그리고 이 step의 CRITICAL: 주문 전 반드시 리스크 게이트를 통과해야 한다.**

### Step A. SPEC — `specs/executor_agent.md`

- `OrderRequest(symbol, side: Literal["buy","sell"], quantity: int, limit_price: float | None)`.
- `Fill(symbol, side, quantity, requested_price, filled_price, slippage)` — `slippage = filled_price - requested_price` (방향 고려).
- `OrderProvider` 인터페이스: `async def place_order(req: OrderRequest) -> Fill`.
  - `MockOrderProvider`: 결정론적 체결(요청가 근처 + 고정 슬리피지). 실제 거래 없음.
  - `RobinhoodOrderProvider`: 골격만, 키 없으면 명확한 예외. **실제 주문 실행 금지.**
- `ExecutorAgent(Agent)`:
  - 생성자에 `AgentRegistry`, `OrderProvider`, 그리고 **리스크 게이트 함수**(`check_risk_gate` 또는 `RiskAgent.evaluate`) 주입.
  - `async def execute(req: OrderRequest) -> Fill | None`:
    - CRITICAL 순서: ① registry.killed면 즉시 거부(None) ② 리스크 게이트 호출 → 차단이면 주문 거부(None) + 사유 기록 ③ 통과 시에만 provider.place_order ④ Fill 기록(슬리피지 포함).
    - 리스크 게이트가 예외를 던지면 **fail-closed**(주문 거부).
  - 체결 내역을 내부 리스트/콜백으로 보관(리포트/알림 에이전트가 후속에서 사용).
- 엣지케이스: quantity 0/음수 → 거부, killed 상태, 게이트 차단, provider 예외.

### Step B. TEST (Red) — `tests/test_executor_agent.py`

- 리스크 게이트 통과 + 정상 요청 → Fill 반환, 슬리피지 계산 검증.
- 게이트 차단(예: RISK_KILL_SWITCH on 또는 evaluate False) → 주문 **거부(None)**, provider.place_order **호출 안 됨**(mock 호출 카운트로 검증).
- registry.killed=True → 거부, provider 미호출.
- quantity 0/음수 → 거부.
- 게이트 함수가 예외 → fail-closed(거부).

### Step C. 구현 (Green) — `agents/executor.py`

- 위 CRITICAL 순서를 코드로 명확히. 게이트 통과 없이는 어떤 경로로도 place_order에 도달하지 못하게.

### Step D. 리팩터

슬리피지 계산·기록 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_executor_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 특히 "게이트 차단 시 provider 미호출" 테스트가 통과해야 한다.
2. 아키텍처 체크리스트:
   - 주문이 리스크 게이트를 반드시 통과하는가? 게이트 차단/killed/예외 시 절대 주문이 나가지 않는가? (CLAUDE.md CRITICAL / ADR-003)
   - 실제 Robinhood 주문 코드가 실행 경로에 없는가?
3. `phases/1-agents/index.json`의 step 4를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 리스크 게이트를 우회하는 주문 경로를 만들지 마라. 이유: 시스템 최대 위험 — 한도 초과 실거래 (CLAUDE.md CRITICAL).
- 게이트 차단/예외 시 주문을 진행하지 마라. fail-closed.
- `RobinhoodOrderProvider`에 실제 주문 로직을 채우지 마라. 골격 + 명확한 예외까지만. 이유: 키/인증은 후속 phase blocked 항목, 잘못하면 실거래.
- quantity 검증(0/음수 거부)을 빠뜨리지 마라.
- 기존 테스트를 깨뜨리지 마라.
