# Step 1: goal-plan-service (AI 결합 — 근거 생성)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ADR.md` (ADR-005: Claude는 설명/판단, 결정론 로직이 우선 / ADR-003: 하드캡)
- `/algorithms/goal_planner.py`, `/specs/goal_planner.md` (step 0 — `derive_settings`, `GoalDerivedSettings`, `PlanMode`, `Feasibility`)
- `/agents/decision.py`, `/algorithms/filters.py` (provider 주입 패턴 — `MockX`/`ClaudeX` 골격 스타일을 그대로 따르라)

## 작업

step 0의 결정론적 역산 세팅에 **AI 근거/요약을 결합**하는 backend 서비스를 만든다. CRITICAL: AI는 설명·요약·소폭 조정 제안만 하고, **최종 세팅은 알고리즘 `derive_settings`가 하드캡으로 clamp한 값**이다. AI가 risk%를 올리지 못한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/goal_plan_service.md`

- `GoalInput(current_equity: float, target_amount: float, months: int, mode: PlanMode)`.
- `GoalPlan`(pydantic): `settings: GoalDerivedSettings`, `rationale: str`, `summary: str`, `feasibility: Feasibility`, `required_monthly_return: float`.
- `GoalPlanProvider` 인터페이스: `async def explain(inp: GoalInput, settings: GoalDerivedSettings) -> str` (근거 텍스트 생성).
  - `MockGoalPlanProvider`: 결정론적 템플릿 근거(예: "월 X% 필요, 모드 Y, 실현가능성 Z → appetite A, risk B%"). 난수·외부호출 없음.
  - `ClaudeGoalPlanProvider`: 골격. `claude-sonnet-4-6` 호출 구조 주석, 키 없으면 명확한 예외. 실제 호출 금지.
- `generate_goal_plan(inp: GoalInput, provider: GoalPlanProvider) -> GoalPlan`:
  - ① `derive_settings`로 결정론 세팅 계산(하드캡 적용) ② provider.explain로 근거 텍스트 ③ GoalPlan 조립.
  - CRITICAL: provider가 무엇을 반환하든 `settings`의 수치(특히 max_risk_pct)는 step 0 결과 그대로다. AI 텍스트가 세팅 수치를 덮어쓰지 못하게 하라.
- 엣지케이스: provider 예외 시 근거 없이도 결정론 세팅은 반환(근거는 fallback 문구), 비현실적 목표 시 summary에 경고 포함.

### Step B. TEST (Red) — `tests/test_goal_plan_service.py`

- `MockGoalPlanProvider`로 `generate_goal_plan` → GoalPlan의 settings가 `derive_settings` 결과와 **정확히 일치**(AI가 수치 변경 못 함) 검증.
- 비현실적 목표 → feasibility UNREALISTIC, max_risk_pct ≤ 하드캡, summary 경고.
- provider.explain 예외 → 결정론 세팅은 그대로 반환되고 근거는 fallback.
- `ClaudeGoalPlanProvider`는 키 없이 호출 시 명확한 예외.

### Step C. 구현 (Green) — `backend/app/services/goal_plan.py`

- provider 주입 패턴(decision/filters와 일관). mock 결정론.
- 세팅 수치의 단일 진실은 `algorithms.goal_planner`. 서비스는 근거를 붙일 뿐.

### Step D. 리팩터

조립·fallback 로직 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_goal_plan_service.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크리스트:
   - 최종 세팅 수치가 알고리즘 `derive_settings` 결과와 일치하는가(AI가 못 바꾸는가)? (ADR-003/005)
   - Claude 의존이 provider 주입으로 격리되고 mock이 결정론적인가?
   - provider 예외 시 결정론 세팅은 안전하게 반환되는가?
3. `phases/3-goal-planner/index.json`의 step 1을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- AI(provider) 반환값으로 `settings`의 risk%/appetite 수치를 덮어쓰지 마라. 이유: AI 환각이 하드캡을 우회해 위험 세팅을 만들 수 있다 (ADR-003/005).
- 실제 Claude API를 호출하지 마라. `MockGoalPlanProvider` 사용.
- `derive_settings`를 서비스에서 재구현하지 마라. 호출만 하라(단일 진실).
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트를 깨뜨리지 마라.
