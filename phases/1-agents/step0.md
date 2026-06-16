# Step 0: agent-base

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md` (에이전트 = 독립 루프, 리스크 에이전트가 kill-switch 보유)
- `/docs/ADR.md` (ADR-002: I/O는 에이전트 레이어로, ADR-003: kill-switch)
- `/docs/PRD.md` (서브에이전트 6개 정의)
- `/agents/risk.py` (기존 리스크 게이트 — 깨뜨리지 마라)

## 작업

모든 서브에이전트가 공유하는 공통 베이스와 레지스트리/킬스위치 버스를 만든다. 이 step은 **개별 에이전트 로직을 구현하지 않는다** — 공통 토대만.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/agent_base.md`

- `AgentStatus` enum: `IDLE | RUNNING | STOPPED | ERROR`.
- `Agent` 추상 베이스 (ABC):
  ```python
  class Agent(ABC):
      name: str
      status: AgentStatus
      def start(self) -> None: ...        # status -> RUNNING
      def stop(self) -> None: ...         # status -> STOPPED
      async def tick(self) -> None: ...   # 1회 작업 사이클 (추상)
  ```
  - 부수효과(외부 I/O)는 각 에이전트가 주입받은 provider로 수행. base 자체는 I/O 없음.
- `AgentRegistry`:
  - 에이전트 등록/조회: `register(agent)`, `get(name)`, `all()`.
  - **kill-switch**: `kill_all(reason: str)` → 등록된 모든 에이전트를 `stop()`하고 `killed=True`, `kill_reason` 기록. `is_killed() -> bool`.
  - `reset()` → kill 상태 해제(수동 복구용).
  - CRITICAL: kill_all은 멱등이어야 한다(여러 번 호출해도 안전). 이유: 리스크 이벤트가 중복 발생할 수 있다.
- 엣지케이스: 중복 이름 등록, 없는 이름 조회, kill 후 start 시도(killed 상태면 start 거부).

### Step B. TEST (Red) — `tests/test_agent_base.py`

- Agent 서브클래스 더미를 만들어 start/stop 상태 전이 검증.
- registry register/get/all, 중복 이름 처리.
- kill_all 호출 시 모든 에이전트가 STOPPED + is_killed True, 멱등성(2회 호출 동일).
- kill 상태에서 agent.start() 거부(또는 무시) 검증.

### Step C. 구현 (Green) — `agents/base.py`

- `Agent`(ABC), `AgentStatus`, `AgentRegistry` 구현.
- 외부 I/O·전역 가변 싱글톤 남용 금지. registry 인스턴스는 명시적으로 생성/주입.

### Step D. 리팩터

상태 전이 로직 정리, 타입 힌트 일관성.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_agent_base.py -v
.venv/bin/python -c "from agents.base import Agent, AgentRegistry, AgentStatus; r=AgentRegistry(); print(r.is_killed())"
```

(venv가 없으면 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` 후 진행)

## 검증 절차

1. 위 AC 커맨드를 실행한다. 기존 88개 테스트도 깨지지 않아야 한다: `.venv/bin/python -m pytest -q`.
2. 아키텍처 체크리스트:
   - base가 외부 I/O 없이 순수 라이프사이클만 다루는가? (ADR-002)
   - kill_all이 멱등인가? (ADR-003)
3. `phases/1-agents/index.json`의 step 0을 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`
   - 개입 필요 → `"blocked"` + `"blocked_reason"`

## 금지사항

- 개별 에이전트(scanner/decision/...) 로직을 구현하지 마라. 이유: 이후 step의 범위다.
- base.py 안에서 파일/네트워크/DB/Claude/MCP를 호출하지 마라. 이유: ADR-002 위반, 테스트 불가.
- `agents/risk.py`의 기존 `check_risk_gate`를 삭제·변경하지 마라. 이유: PreToolUse hook이 의존한다.
- 기존 테스트를 깨뜨리지 마라.
