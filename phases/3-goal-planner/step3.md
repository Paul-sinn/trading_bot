# Step 3: goals-page-ai-analysis (프론트 — AI 분석하기 버튼/패널)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (⑤ 목표 & 리스크, 색상·컴포넌트·AI 슬롭 안티패턴)
- `/frontend/src/app/goals/page.tsx` (기존 목표 페이지 — 유지하고 확장)
- `/frontend/src/components/ui/`, `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts`
- `/specs/goal_plan_api.md` (step 2 — `POST /api/goal-plan` 요청/응답 스키마)

## 작업

기존 목표 페이지(`/goals`)를 **유지한 채**, 목표탭 안에 "AI 분석하기" 버튼/패널을 추가한다. SDD → TDD.

### Step A. SPEC — `specs/goals_ai_analysis.md`

기존 목표 UI(진행 바·한도 설정)는 그대로 두고 아래를 추가:
- **목표기간(개월) 입력**: 숫자 입력/슬라이더 (UI_GUIDE 입력 스타일).
- **모드 선택**: `안전 한도 내 (추천)` ↔ `목표 우선(공격적)` 라디오/토글. "추천" 라벨을 안전 모드에 표시.
- **"AI 분석하기" 버튼**: 클릭 시 `POST /api/goal-plan`(목표금액·기간·모드) 호출 → 결과를 패널에 표시. 백엔드 미가동 시 `lib/mock.ts`의 mock 계획으로 graceful fallback(크래시 금지).
- **결과 패널**: 역산된 세팅(투자성향 appetite, 리스크%, 드로우다운 한도, 최대 포지션, 스탑 배수) + AI 근거(rationale) + 실현가능성 배지(현실적/도전적/비현실적, 색: 녹/주/적).
- **"적용" 버튼**: 클릭 시 적용(이 단계에서는 로컬 상태 반영 또는 `POST /api/goal-plan/apply` 호출; 백엔드 없으면 로컬). 적용 전에는 활성 세팅을 바꾸지 않는다(검토 후 적용).

### Step B. TEST (Red) — `frontend/src/__tests__/goals_ai_analysis.test.tsx`

- 목표기간 입력 + 모드 선택(안전/공격) 렌더, "안전 한도 내 (추천)" 라벨 존재.
- "AI 분석하기" 버튼 클릭 → 결과 패널에 세팅 항목들 + 실현가능성 배지 + 근거 표시(mock fetch).
- "적용" 버튼 존재, 클릭 동작.
- 백엔드 fetch 실패 시 mock fallback으로 크래시 없이 렌더.
- 기존 목표 진행 바/한도 설정도 여전히 존재(회귀 없음).

### Step C. 구현 (Green) — `frontend/src/app/goals/page.tsx` 확장 + 컴포넌트

- `frontend/src/components/goals/AiAnalysisPanel.tsx`(Client Component) 추가. 기존 페이지 구조는 보존.
- `lib/api.ts`에 goal-plan 호출 함수, `lib/mock.ts`에 mock 계획 추가. 타입은 `types/index.ts`에 추가(백엔드 스키마와 동기).

### Step D. 리팩터

패널·배지·세팅 표시를 작은 컴포넌트로 분리. 기존 프리미티브 재사용.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

## 검증 절차

1. 위 AC 실행(build/lint/test 통과). 6개 페이지 라우트가 여전히 모두 빌드되는지 확인.
2. 아키텍처 체크리스트:
   - 기존 목표 UI를 유지(회귀 없음)하고 AI 패널을 추가했는가?
   - 모드 선택에 "안전 한도 내 (추천)" 라벨이 있는가?
   - 백엔드 없이 graceful fallback 하는가? AI 슬롭 안티패턴 미사용?
   - frontend가 Claude/Robinhood를 직접 호출하지 않고 backend만 부르는가? (CLAUDE.md CRITICAL)
3. `phases/3-goal-planner/index.json`의 step 3을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 기존 목표 페이지의 진행 바·한도 설정을 제거하지 마라. 이유: "지금처럼 유지 + 추가" 요구.
- "적용" 전에 활성 세팅을 바꾸지 마라. 이유: 검토 후 적용 원칙.
- frontend에서 Claude/Robinhood를 직접 호출하지 마라. backend `/api/goal-plan`만. (CLAUDE.md CRITICAL)
- 새 디자인 토큰/프리미티브를 만들지 마라. step 0(phase2)의 것을 재사용.
- 다른 페이지를 건드리지 마라. 기존 테스트(Python + frontend)를 깨뜨리지 마라.
