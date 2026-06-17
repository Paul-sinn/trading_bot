# SPEC: goal_plan_service (목표 플랜 — AI 근거 결합 서비스)

step 0의 결정론적 역산 세팅(`algorithms.goal_planner.derive_settings`)에 **AI 근거/요약을 결합**하는
backend 서비스. CRITICAL: AI는 설명·요약만 하고, **최종 세팅 수치는 알고리즘이 하드캡으로 clamp한 값**
그대로다. AI가 risk%/appetite를 덮어쓰지 못한다.

관련 문서: PRD(목표 & 리스크, AI 분석), ADR-001(외부 API는 backend service에 격리),
ADR-003(리스크 하드캡 — 최우선), ADR-005(Claude는 설명/판단, 결정론 로직이 우선), ADR-006(SDD→TDD).
재사용: `algorithms/goal_planner.py`(`derive_settings`, `GoalDerivedSettings`, `PlanMode`, `Feasibility`),
provider 주입 패턴(`agents/decision.py`, `algorithms/filters.py`의 `MockX`/`ClaudeX` 골격 스타일).

CRITICAL (ADR-003/005): 세팅 수치의 **단일 진실은 `algorithms.goal_planner`**다. 서비스는 근거 텍스트를
붙일 뿐, provider가 무엇을 반환하든 `GoalPlan.settings`는 `derive_settings` 결과 그대로다.
AI 환각이 하드캡을 우회해 위험 세팅을 만들지 못하게 한다.

## 데이터 모델

```python
@dataclass(frozen=True)
class GoalInput:
    current_equity: float
    target_amount: float
    months: int
    mode: PlanMode

class GoalPlan(BaseModel):           # pydantic — API 직렬화 대상
    settings: GoalDerivedSettings    # derive_settings 결과 그대로 (수치 단일 진실)
    rationale: str                   # AI(또는 fallback) 근거 텍스트
    summary: str                     # 결정론적 요약(비현실 목표 시 경고 포함)
    feasibility: Feasibility         # settings.feasibility와 동일
    required_monthly_return: float   # settings.required_monthly_return와 동일
```

> `GoalDerivedSettings`/`RiskLimits`는 stdlib frozen dataclass다. `GoalPlan`은
> `arbitrary_types_allowed=True`로 이를 **재검증/coerce 없이** 그대로 담는다(단일 진실 보존).

## provider 인터페이스 (외부 의존 주입 — ADR-005)

```python
@runtime_checkable
class GoalPlanProvider(Protocol):
    async def explain(inp: GoalInput, settings: GoalDerivedSettings) -> str: ...
```

### `MockGoalPlanProvider`
- 결정론적 템플릿 근거. 난수·외부호출 없음.
- 예: `"월 3.1% 필요, 모드 aggressive, 실현가능성 realistic → appetite 0.39, risk 2.4%."`

### `ClaudeGoalPlanProvider`
- 실연동 골격(decision/filters의 `ClaudeX`와 동일 스타일).
- `__init__(api_key=None)`. 키 없으면 `explain` 호출 시 명확한 `ValueError`.
  키가 있어도 실호출하지 않고 `NotImplementedError`.
- 실연동 구조는 주석으로만(`claude-sonnet-4-6` messages.create → 응답을 근거 텍스트로).

## 함수

### `async generate_goal_plan(inp: GoalInput, provider: GoalPlanProvider) -> GoalPlan`
1. `settings = derive_settings(inp.current_equity, inp.target_amount, inp.months, inp.mode)`
   (입력 무효 시 `ValueError` 전파 — `current_equity<=0`/`months<=0`).
2. `rationale = await provider.explain(inp, settings)`.
   - provider 예외 시: 결정론 세팅은 그대로 두고, `rationale`은 **fallback 문구**로 대체(서비스가 죽지 않음).
3. `summary`: `settings`로부터 **결정론적**으로 조립. `feasibility == UNREALISTIC`이면 경고 문구 포함.
4. `GoalPlan(settings=settings, rationale=rationale, summary=summary,
   feasibility=settings.feasibility, required_monthly_return=settings.required_monthly_return)`.

CRITICAL: 2단계 provider 반환값/예외는 `settings`(특히 `risk_limits.max_risk_pct`)에 **영향을 주지 않는다**.

## 불변식
- `plan.settings`는 동일 입력에 대한 `derive_settings` 결과와 **정확히 일치**(AI가 못 바꾼다).
- 어떤 입력·provider에서도 `plan.settings.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT`.
- `plan.feasibility == plan.settings.feasibility`, `plan.required_monthly_return == plan.settings.required_monthly_return`.

## 엣지케이스
- provider.explain 예외 → 결정론 세팅 그대로 반환, `rationale`은 fallback 문구.
- 비현실적 목표(예: 1개월 10배) → `feasibility=UNREALISTIC`, `max_risk_pct<=하드캡`, `summary`에 경고.
- 입력 무효(`current_equity<=0`/`months<=0`) → `derive_settings`에서 `ValueError` 전파.

## 비범위 (이 step에서 하지 않음)
- 실제 Claude API 호출(`MockGoalPlanProvider`만 사용).
- API 라우트(step 2), 프론트 페이지(step 3).
- `derive_settings` 재구현(호출만 — 단일 진실).
