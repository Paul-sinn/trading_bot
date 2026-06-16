# SPEC: agent_base (서브에이전트 공통 베이스 · 레지스트리 · kill-switch 버스)

모든 서브에이전트(scanner/decision/executor/risk/reporter/notifier)가 공유하는 공통 토대.
이 모듈은 **개별 에이전트 로직을 구현하지 않는다** — 라이프사이클 인터페이스와 레지스트리/킬스위치 버스만 제공한다.

관련 문서: ARCHITECTURE(에이전트 = 독립 루프, `base.Agent` 인터페이스 `start/stop/tick`, 리스크 에이전트가
다른 에이전트의 kill-switch 보유), ADR-002(I/O는 에이전트 레이어로 — base 자체는 부수효과 없음),
ADR-003(kill-switch는 한도 초과 시 전 에이전트 강제 정지, 안전 최우선), ADR-006(SDD→TDD).

CRITICAL (ADR-002): `base.py`는 파일/네트워크/DB/Claude/MCP 등 외부 I/O를 직접 호출하지 않는다.
부수효과는 각 에이전트가 주입받은 provider로 수행한다. base는 순수 라이프사이클·레지스트리만 다룬다.

CRITICAL (ADR-003): `AgentRegistry.kill_all`은 **멱등**이어야 한다. 리스크 이벤트가 중복 발생해도
여러 번 호출이 안전해야 하며, 한 번이라도 kill되면 `is_killed()`는 True를 유지한다.

CRITICAL (ADR-003): kill 상태에서는 `Agent.start()`를 거부한다(killed 에이전트는 RUNNING으로 못 간다).
안전이 최우선 — kill-switch가 걸린 동안 어떤 에이전트도 재가동되지 않는다.

## AgentStatus (enum)

```python
class AgentStatus(Enum):
    IDLE = "idle"        # 생성 직후 기본 상태
    RUNNING = "running"  # start() 후
    STOPPED = "stopped"  # stop() 또는 kill_all() 후
    ERROR = "error"      # 에이전트 내부 오류 표시(이 step에서는 전이만 허용, 자동 설정 안 함)
```

## Agent (추상 베이스, ABC)

```python
class Agent(ABC):
    name: str
    status: AgentStatus       # 생성 시 IDLE
    killed: bool              # kill 신호를 받았는지(기본 False)

    def start(self) -> None     # killed가 아니면 status -> RUNNING. killed면 거부(상태 변화 없음).
    def stop(self) -> None      # status -> STOPPED.
    def mark_killed(self) -> None  # killed=True 표시 후 stop(). 멱등.
    async def tick(self) -> None   # 추상 — 1회 작업 사이클. 서브클래스가 구현.
```

- `__init__(self, name: str)`: `name` 설정, `status=IDLE`, `killed=False`.
- `start()`:
  - `killed`가 True면 아무 일도 하지 않는다(상태 유지). 안전상 재가동 금지.
  - 아니면 `status = RUNNING`.
- `stop()`: `status = STOPPED` (이미 STOPPED여도 안전, 멱등).
- `mark_killed()`: `killed = True`로 표시하고 `stop()` 호출. 여러 번 호출해도 동일(멱등).
- `tick()`: `@abstractmethod`, `async`. base는 구현하지 않는다(외부 I/O는 서브클래스 몫).
- base 자체는 I/O·전역 가변 싱글톤 사용 금지.

## AgentRegistry

명시적으로 생성/주입하는 인스턴스(전역 싱글톤 강제 안 함). kill-switch 버스 역할.

```python
class AgentRegistry:
    def register(self, agent: Agent) -> None
    def get(self, name: str) -> Agent
    def all(self) -> list[Agent]
    def kill_all(self, reason: str) -> None
    def is_killed(self) -> bool
    def reset(self) -> None
```

- `__init__`: 빈 레지스트리. `killed=False`, `kill_reason=None`.
- `register(agent)`: 이름→에이전트 등록. **중복 이름 등록은 `ValueError`**.
  - CRITICAL: 이미 kill 상태인 레지스트리에 등록되는 에이전트는 즉시 `mark_killed()` 된다(kill 일관성).
- `get(name)`: 등록된 에이전트 반환. **없으면 `KeyError`**.
- `all()`: 등록된 모든 에이전트 리스트(등록 순서 보존).
- `kill_all(reason)`:
  - 등록된 모든 에이전트에 `mark_killed()` 호출(→ killed=True + STOPPED).
  - `self.killed = True`, `self.kill_reason = reason` 기록.
  - **멱등(ADR-003)**: 여러 번 호출해도 안전. `kill_reason`은 **최초 호출** 사유를 유지한다
    (중복 리스크 이벤트가 원래 차단 사유를 덮어쓰지 않게).
- `is_killed()`: `self.killed` 반환.
- `reset()`: `killed=False`, `kill_reason=None`로 해제(수동 복구용). 에이전트 상태는 강제로 되돌리지 않는다
  (재가동은 운영자가 명시적으로 `start()` 호출). 단, 각 에이전트의 `killed` 플래그도 해제한다(재가동 허용 위해).

## 엣지케이스

- 중복 이름 `register` → `ValueError`.
- 없는 이름 `get` → `KeyError`.
- kill 후 `agent.start()` → 거부(status 변화 없음, RUNNING 안 됨).
- `kill_all` 2회 호출 → 동일 결과(멱등), `kill_reason`은 최초 사유 유지.
- 이미 kill된 레지스트리에 `register` → 그 에이전트도 즉시 killed.
- `reset()` 후 `start()` → 정상적으로 RUNNING 가능.

## 비범위 (이 step에서 하지 않음)

- 개별 에이전트(scanner/decision/executor/risk/reporter/notifier) 로직 — 이후 step.
- 실제 주기 실행 루프 스케줄링, asyncio 이벤트 루프 구동.
- 실시간 리스크% 계산(리스크 에이전트, step 1). `agents/risk.py`의 `check_risk_gate`는 건드리지 않는다.
- I/O(파일/네트워크/DB/Claude/MCP).
