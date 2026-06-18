# Step 1: llm-providers-core (목표플랜 + 판단 → OpenAI)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/ADR.md` (ADR-007 OpenAI / ADR-003·005: LLM은 근거만, 안전 수치는 알고리즘)
- `/backend/app/services/llm.py`, `/specs/openai_client.md` (step 0 — `LLMClient`, `make_llm_client`)
- `/backend/app/services/goal_plan.py`, `/specs/goal_plan_service.md` (`ClaudeGoalPlanProvider` → 교체 대상)
- `/agents/decision.py`, `/specs/decision_agent.md` (`ClaudeDecisionProvider` → 교체 대상, `Decision`, `DecisionResult`)

## 작업

핵심 LLM 기능 2개(목표플랜 근거, 판단 에이전트)의 Claude 골격을 **OpenAI 실연동**으로 교체한다.

**SDD → TDD 순서를 강제한다.**

### 공통 원칙 (CRITICAL)

- LLM(OpenAI)은 **텍스트(근거/판단 라벨)만** 생성한다. 목표플랜의 세팅 수치(특히 max_risk_pct)는 `derive_settings`가 단일 진실이며 LLM이 못 바꾼다(ADR-003/005).
- 키 부재(`make_llm_client`가 None) → **Mock provider로 안전 fallback**. 즉 OpenAI provider는 LLMClient를 주입받고, client가 없으면 호출부가 Mock을 쓴다.
- 판단 에이전트: LLM 응답이 불확실/파싱 불가/예외면 **HOLD(보수적 안전 기본값)**.

### Step A. SPEC 업데이트 — `specs/goal_plan_service.md`, `specs/decision_agent.md`

- `ClaudeGoalPlanProvider` → `OpenAIGoalPlanProvider(LLMClient)`: `explain()`이 OpenAI로 근거 텍스트 생성. 세팅 수치는 입력 그대로 설명만.
- `ClaudeDecisionProvider` → `OpenAIDecisionProvider(LLMClient)`: `decide()`가 OpenAI로 BUY/HOLD/SELL + rationale 생성. 응답을 안전하게 파싱(불명확 → HOLD), confidence [0,1] clamp.
- provider 선택 헬퍼: 키 있으면 OpenAI, 없으면 Mock.

### Step B. TEST (Red) — `tests/test_goal_plan_service.py`, `tests/test_decision_agent.py` 갱신/추가

- **네트워크 금지**. fake `LLMClient`(고정 텍스트 반환) 주입으로 검증.
- `OpenAIGoalPlanProvider`: fake client가 근거를 반환 → GoalPlan.rationale에 반영되고, **settings 수치는 derive_settings 결과 그대로**(AI가 못 바꿈).
- `OpenAIDecisionProvider`: fake client가 "BUY" 류 응답 → Decision.BUY. 파싱 불가/예외 응답 → HOLD. confidence clamp.
- 키 없을 때 provider 선택이 Mock으로 fallback.
- 기존 Mock provider 테스트는 유지(회귀).

### Step C. 구현 (Green)

- `backend/app/services/goal_plan.py`, `agents/decision.py`에 OpenAI provider 추가(기존 Mock 유지). LLM 호출은 step 0의 `LLMClient` 사용.
- 기존 `Claude*Provider`는 제거하거나 `OpenAI*Provider`로 대체(네이밍 정리). 남길 경우 deprecated 주석.

### Step D. 리팩터

프롬프트 빌드·응답 파싱 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_goal_plan_service.py tests/test_decision_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크리스트:
   - LLM이 목표플랜 세팅 수치를 못 바꾸는가(derive_settings 단일 진실)? (ADR-003/005)
   - 판단 불확실 시 HOLD인가?
   - 키 없을 때 Mock fallback인가? 테스트가 네트워크 없이 도는가?
3. `phases/4-integration/index.json`의 step 1을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- LLM 응답으로 목표플랜의 risk%/appetite 수치를 덮어쓰지 마라. 이유: 환각이 하드캡 우회 → 위험 세팅 (ADR-003/005).
- 판단 LLM이 불확실/예외일 때 BUY/SELL로 처리하지 마라. HOLD가 안전 기본값.
- 테스트에서 실제 OpenAI를 호출하지 마라. fake LLMClient 주입.
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트(Mock provider 등)를 깨뜨리지 마라.
