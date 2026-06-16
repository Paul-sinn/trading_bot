# Step 5: goals-risk-page (⑤ 목표 & 리스크)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (⑤ 목표 & 리스크, 게이지/진행 바 규칙)
- `/docs/PRD.md` (Layer 3 사이징·리스크 한도 개념)
- `/frontend/src/components/ui/` (Gauge/진행 바/입력 — step 0)
- `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/agents/risk.py` (RiskLimits: max_risk_pct/max_drawdown_pct/max_position_pct — 설정 항목 동기화)

## 작업

목표 & 리스크 페이지(`frontend/src/app/goals/page.tsx`). **SDD → TDD**: `specs/goals_risk_page.md` → 스모크 테스트(Red) → 구현.

요소 (UI_GUIDE ⑤):
- **목표금액 진행 바**: 현재/목표 대비 진행률(진행 바 프리미티브, 퍼센트 병기).
- **드로우다운 한도 / 최대 포지션 크기 설정**: 입력 필드(UI_GUIDE 입력 스타일). 값은 `RiskLimits` 항목과 매핑.
- 설정 변경은 로컬 상태로 반영(저장은 backend — 후속 연동). mock 기본값.
- Client Component(입력 인터랙션).

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/goals.test.tsx` — 목표 진행 바 렌더, 드로우다운/최대포지션 입력 필드 존재, 입력 변경이 상태에 반영.

## 검증 절차

1. AC 실행.
2. 체크리스트: 진행 바 + 한도 설정 입력 포함? RiskLimits 항목과 일치? UI_GUIDE 입력/게이지 스타일?
3. `phases/2-frontend/index.json`의 step 5 업데이트.

## 금지사항

- 설정값을 frontend에서 직접 거래 로직에 적용하지 마라. 이유: backend 권위. UI는 입력/표시까지.
- 새 프리미티브 만들지 마라. 다른 페이지를 건드리지 마라. 기존 테스트를 깨뜨리지 마라.
