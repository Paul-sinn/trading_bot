# Step 4: direction-ai-page (④ 방향성 & AI 분석)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (④ 방향성 & AI 분석, 방향성=강세/중립/약세 색)
- `/docs/PRD.md` (핵심기능 4: AI 시황 분석, 매일 9시 Claude 시황 요약 + 7일 방향성)
- `/frontend/src/components/ui/`, `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/agents/decision.py` (Decision enum / DecisionResult — 방향성 라벨/근거 구조 참고)

## 작업

방향성 & AI 분석 페이지(`frontend/src/app/direction/page.tsx`). **SDD → TDD**: `specs/direction_ai_page.md` → 스모크 테스트(Red) → 구현.

요소 (UI_GUIDE ④):
- **매일 9시 Claude 시황 요약**: 텍스트 요약 카드 (생성 시각 표시). mock 시황 텍스트.
- **다음 7일 예상 방향**: 강세 / 중립 / 약세 라벨 + 근거 카드. 라벨 색 = 강세 녹색·중립 중립색·약세 적색.
- 데이터는 `lib/mock.ts`의 시황/방향성 mock 기본 + api 시도 후 fallback. (실제 Claude 호출은 backend 책임 — frontend는 결과만 표시.)

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/direction.test.tsx` — 시황 요약 카드 렌더, 방향성 라벨(강세/중립/약세 중 하나) + 근거 카드 존재.

## 검증 절차

1. AC 실행.
2. 체크리스트: 시황 요약 + 7일 방향성 라벨/근거 포함? 방향성 색상 규칙? AI 슬롭 미사용("Powered by AI" 배지 금지)?
3. `phases/2-frontend/index.json`의 step 4 업데이트.

## 금지사항

- "Powered by AI" 배지나 보라색 브랜딩을 넣지 마라. 이유: UI_GUIDE AI 슬롭 안티패턴.
- frontend에서 Claude를 직접 호출하지 마라. 이유: CLAUDE.md CRITICAL. backend 결과만 표시.
- 새 프리미티브 만들지 마라. 다른 페이지를 건드리지 마라. 기존 테스트를 깨뜨리지 마라.
