# Step 3: goals-page-restructure (목표&리스크 탭 — 수동 칸 + AI 분석 칸 분리)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (⑤ 목표 & 리스크, 카드·입력 스타일, AI 슬롭 안티패턴)
- `/frontend/src/app/goals/page.tsx` (현재 목표 페이지)
- `/frontend/src/components/goals/AiAnalysisPanel.tsx` (현재 AI 패널 — `targetAmount`를 prop으로 받음)
- `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts`

## 작업

목표 & 리스크 탭(`/goals`)을 사용자 요구에 맞게 재구성한다. **투자성향 설정 탭(`/profile`)은 절대 건드리지 마라.**

요구사항:
1. **AI 분석하기 칸에 "목표금액" 입력 추가**: 현재 `AiAnalysisPanel`은 목표금액을 prop으로만 받는다. 패널 안에 목표금액 입력 필드를 추가해 사용자가 패널에서 직접 목표금액·목표기간·모드를 정하고 분석하게 한다.
2. **탭을 두 칸으로 분리**: 목표 페이지를 시각적으로 명확히 구분된 **두 섹션**으로 구성한다.
   - **수동 설정 칸**: 기존 목표금액 진행 바·드로우다운 한도·최대 포지션 크기 등 직접 설정 UI(기존 것 유지).
   - **AI 분석 칸**: `AiAnalysisPanel`(목표금액+기간+모드 입력 → "AI 분석하기" → 역산 세팅·근거·실현가능성 → "적용").
   - 두 칸은 별도 카드/섹션으로 구분(제목 라벨 포함, 예: "직접 설정" / "AI 목표 분석").

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/goals_ai_analysis.md` 갱신

- `AiAnalysisPanel`이 자체 목표금액 입력을 갖도록 시그니처 변경(prop 의존 제거 또는 기본값). 요청 body에 사용자가 입력한 target_amount 사용.
- 목표 페이지의 두 섹션 구조와 각 섹션 책임 명시.

### Step B. TEST (Red) — `frontend/src/__tests__/goals_ai_analysis.test.tsx` 갱신 + 페이지 테스트

- AI 패널에 목표금액 입력 필드가 렌더되고, 변경 시 분석 요청에 반영.
- 목표 페이지에 "직접 설정" 섹션(진행 바·한도 입력)과 "AI 목표 분석" 섹션이 **둘 다** 존재.
- 기존 AI 분석 흐름(분석→결과→적용) 회귀 없음.
- 백엔드 미가동 시 mock fallback 유지.

### Step C. 구현 (Green)

- `AiAnalysisPanel.tsx`에 목표금액 입력 state 추가, `buildRequest`가 그 값을 사용.
- `goals/page.tsx`를 두 섹션으로 재구성(기존 수동 설정 UI 보존 + AI 패널 섹션).
- 기존 프리미티브/스타일 재사용(새 디자인 토큰 금지).

### Step D. 리팩터

섹션 컴포넌트 분리, 중복 제거.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

(주의: dev 서버가 떠 있으면 끄고 빌드하라 — dev 중 build는 `.next` 충돌로 CSS가 깨진다. CLAUDE.md 실수 기록 참조.)

## 검증 절차

1. 위 AC 실행(build/lint/test 통과). 6개 페이지 라우트가 여전히 모두 빌드되는지 확인.
2. 아키텍처 체크리스트:
   - AI 패널에 목표금액 입력이 있는가? 목표 탭이 수동/AI 두 섹션으로 분리됐는가?
   - `/profile`(투자성향) 페이지를 건드리지 않았는가?
   - 기존 수동 설정 UI가 유지되는가(회귀 없음)? AI 슬롭 안티패턴 미사용?
3. `phases/4-integration/index.json`의 step 3을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- `/profile`(투자성향 설정) 페이지를 수정하지 마라. 이유: 사용자가 "일단 냅두라"고 명시.
- 기존 수동 설정(진행 바·한도)을 제거하지 마라. 두 칸이 공존해야 한다.
- dev 서버를 켠 채 `npm run build`를 돌리지 마라. 이유: `.next` 충돌로 스타일 깨짐(CLAUDE.md 실수 기록).
- 새 디자인 토큰/프리미티브를 만들지 마라. 다른 페이지를 건드리지 마라. 기존 테스트를 깨뜨리지 마라.
