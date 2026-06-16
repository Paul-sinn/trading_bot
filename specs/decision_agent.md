# SPEC: decision_agent (판단 에이전트 — 후보 종합 판단 매수/홀드/매도)

판단 에이전트는 스캐너가 만든 후보(`Candidate`)에 대해 차트 시그널·필터 결과·(있으면)
뉴스 요약을 종합해 **매수(BUY) / 홀드(HOLD) / 매도(SELL)** 를 결정한다. 알고리즘 3레이어를
모두 통과한 후보만 여기로 오며, 이 에이전트가 자동매매 루프의 **최종 판단 게이트**다.

관련 문서: PRD(판단 = Claude API 호출, 뉴스+차트 종합, 매수/홀드/매도),
ARCHITECTURE(자동매매 루프: 후보 → 판단 에이전트(Claude) → 매수/홀드/매도),
ADR-005(Claude는 최종 게이트 / 알고리즘이 1차 필터), `specs/agent_base.md`(Agent·AgentRegistry),
`specs/scanner_agent.md`(Candidate 정의).

CRITICAL: 실제 Claude API를 호출하지 않는다. 이 phase는 결정론적 `MockDecisionProvider`만
사용한다(키 부재 + 비결정론으로 테스트 불가). 실호출은 provider 주입으로 격리한다(ADR-005).

CRITICAL: 알고리즘 시그널/필터 판정을 여기서 다시 구현하지 않는다. `Candidate`에 담긴
Layer 1/2 결과(`signal`, `filters_passed`)를 활용만 한다(단일 진실).

CRITICAL: 불확실하거나 provider 예외가 나면 보수적으로 **HOLD** 로 처리한다. 불확실할 때
매매(BUY/SELL)하면 위험하다 — HOLD가 안전 기본값이다.

## 데이터 모델

```python
class Decision(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass(frozen=True)
class DecisionInput:
    candidate: Candidate     # 스캐너 Layer 1+2 통과 후보
    context: dict            # 차트 시그널·필터 결과·(있으면) 뉴스 요약 등 부가 맥락


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    confidence: float        # 0~1 클램프
    rationale: str           # 판단 근거(짧은 설명)
```

`Candidate`는 `agents.scanner`를, `SignalResult`/`Signal`은 `algorithms.signals`를 재사용한다.

## Provider 인터페이스 (외부 의존 주입)

### `DecisionProvider`
```python
class DecisionProvider(Protocol):
    async def decide(self, inp: DecisionInput) -> DecisionResult: ...
```
- 실거래 provider(Claude API 연동)는 후속 phase. 이 step은 `MockDecisionProvider`만 사용.

### `MockDecisionProvider`
- **결정론적** 규칙: `signal.overall == BULLISH` **이고** `filters_passed == True` → BUY,
  아니면 HOLD. 난수·외부 호출 없음.
- BUY는 높은 confidence, HOLD는 낮은 confidence를 부여(결정론).

### `ClaudeDecisionProvider`
- `claude-sonnet-4-6` 호출 구조는 **주석으로만** 남긴다(골격). 실제 호출 금지.
- 키가 없으면 명확한 예외(`ValueError`), 키가 있어도 실호출하지 않고 `NotImplementedError`.

## DecisionAgent(Agent)

```python
class DecisionAgent(Agent):
    def __init__(self, registry: AgentRegistry, provider: DecisionProvider,
                 *, name: str = "decision") -> None: ...
    async def decide_candidates(self, candidates: list[Candidate]) -> list[DecisionResult]: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속(IDLE/RUNNING/STOPPED, kill 후 start 거부).
- `decide_candidates(candidates) -> list[DecisionResult]`:
  1. `registry.is_killed()`이면 **즉시 빈 리스트** 반환(판단 스킵).
  2. 빈 후보면 빈 리스트.
  3. 각 후보를 `DecisionInput`으로 감싸 `provider.decide(...)`에 위임.
  4. **한 후보에서 provider 예외가 나면 그 후보는 HOLD(안전 기본값)** 로 처리하고, 나머지
     후보는 정상 판단한다(격리 + 보수적 처리).
  5. 결과 `confidence`는 0~1로 클램프한다.
- `tick()` — 에이전트 루프 1회: 현재 step은 후보 소스 연결 전이므로 빈 입력으로 동작
  (`latest_results`를 빈 리스트로 갱신). 후속 step에서 스캐너 결과와 연결한다.

## 엣지케이스

- 빈 후보 → `decide_candidates`는 빈 리스트.
- provider 예외 후보 → 그 후보만 HOLD(confidence 0.0, rationale 명시), 나머지는 정상.
- `confidence`가 0~1 밖(예: 1.5, -0.2) → 0~1로 클램프.
- `registry.is_killed()` → 즉시 빈 리스트(판단 스킵).
- `ClaudeDecisionProvider` 키 없이 호출 → 명확한 예외.

## 비범위 (이 step에서 하지 않음)

- 실제 Claude API 호출·인증(주입 Mock provider만 사용).
- 주문 실행/리스크 게이트(후속 executor/risk 에이전트).
- 스캐너 결과와의 실제 배선(tick은 빈 입력 단위만 제공).
