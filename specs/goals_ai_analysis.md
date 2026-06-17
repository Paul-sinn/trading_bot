# SPEC: goals_ai_analysis (프론트 — 목표탭 "AI 분석하기" 버튼/패널)

⑤ 목표 & 리스크 페이지(`/goals`)를 **유지한 채**, 목표탭 안에 "AI 분석하기" 패널을 추가한다.
사용자가 목표기간·모드를 정하고 분석을 요청하면 backend `POST /api/goal-plan`(step 2)으로 역산된
세팅 + AI 근거 + 실현가능성을 받아 **검토용으로** 표시하고, "적용"을 눌러야 반영한다.

관련 문서: UI_GUIDE(⑤, 색상·컴포넌트·AI 슬롭 안티패턴), ARCHITECTURE(backend SSOT — 프론트는
backend REST만 호출), CLAUDE.md CRITICAL(프론트는 Claude/Robinhood 직접 호출 금지),
ADR-003/005(세팅 수치 단일 진실은 backend, 하드캡 보존), step 2 `specs/goal_plan_api.md`.

재사용: `components/ui/{Card,Button}`, `lib/utils.cn`, step 0(phase2) 디자인 토큰. 새 토큰/프리미티브 금지.

## 기존 UI (유지 — 회귀 금지)
- 목표금액 진행 바(`data-testid="goal-progress"`).
- 드로우다운/최대 포지션 한도 입력(`input-drawdown`, `input-max-position`).
- 위 요소는 제거·변경하지 않는다. AI 패널은 **추가**된다.

## 추가 UI — `AiAnalysisPanel` (Client Component)
입력
- **목표기간(개월)**: 숫자 입력(`data-testid="input-months"`, UI_GUIDE 입력 스타일). 기본 12.
- **모드 선택**: 라디오 2개 — `안전 한도 내 (추천)`(value `safe`) ↔ `목표 우선(공격적)`(value `aggressive`).
  기본 `safe`. "추천" 라벨은 안전 모드에 표시한다.
- **"AI 분석하기" 버튼**(`data-testid="analyze-btn"`).

동작
- 분석 버튼 클릭 → `createGoalPlan({ target_amount, months, mode })` 호출.
  - `target_amount`는 페이지의 목표금액(현재 mock), `current_equity`는 생략(백엔드가 포트폴리오로 보완).
  - 성공 → 응답 `GoalPlan`을 결과 패널에 표시.
  - 실패/`null`(백엔드 미가동) → `lib/mock.ts`의 `mockGoalPlan`으로 **graceful fallback**(크래시 금지).

결과 패널(`data-testid="ai-result-panel"`)
- 역산 세팅 표시: 투자성향 appetite, 최대 리스크%, 드로우다운 한도%, 최대 포지션%, 스탑 ATR 배수.
  (백엔드 분수값 0.05 → "5.0%"로 표기. 단위 일관.)
- AI 근거(`rationale`, `data-testid="ai-rationale"`).
- 실현가능성 배지(`data-testid="feasibility-badge"`): realistic→`현실적`(녹 #22c55e),
  ambitious→`도전적`(주 #f59e0b), unrealistic→`비현실적`(적 #ef4444).
- **"적용" 버튼**(`data-testid="apply-btn"`): 클릭 시 `applyGoalPlan(동일 입력)` 호출(백엔드 없으면
  무시) 후 로컬 상태로 "적용됨" 표시. **적용 전에는 활성 세팅(한도 입력)을 바꾸지 않는다**(검토 후 적용).

## API (lib/api.ts — backend만 호출)
- `createGoalPlan(req): Promise<GoalPlan | null>` → `POST /api/goal-plan`. 실패 시 null.
- `applyGoalPlan(req): Promise<GoalPlanRecord | null>` → `POST /api/goal-plan/apply`. 실패 시 null.
- CRITICAL: 거래소/Claude를 직접 부르지 않는다. `apiFetch`(기존 graceful 래퍼)만 사용한다.

## 타입 (types/index.ts — 백엔드 스키마 동기)
- `PlanMode = "safe" | "aggressive"`, `Feasibility = "realistic" | "ambitious" | "unrealistic"`.
- `RiskLimits`(분수: max_risk_pct/max_drawdown_pct/max_position_pct).
- `GoalDerivedSettings`(appetite/risk_limits/stop_loss_atr_multiplier/feasibility/required_monthly_return).
- `GoalPlan`(settings/rationale/summary/feasibility/required_monthly_return) — step 2 응답.
- `GoalPlanRequest`(target_amount/months/mode/current_equity?).
- `GoalPlanRecord`(apply 응답 평탄 DTO — id/applied/created_at 포함).

## 불변식 / 엣지케이스
- 백엔드 미가동(`null`)에서도 패널·페이지가 크래시 없이 렌더된다(mock fallback).
- 세팅 수치는 백엔드(또는 mock) 응답을 **그대로 표시**만 한다(프론트에서 재계산/하드캡 우회 금지).
- "적용" 전에는 기존 한도 입력값·활성 세팅이 변하지 않는다.

## 비범위
- 활성 세팅을 거래 로직에 실제 반영(별도 backend 연동) — 이 step은 표시/적용 트리거까지.
- 실제 Claude/Robinhood 연동(backend Mock provider).
- 다른 페이지 변경.
