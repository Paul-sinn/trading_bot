# Step 3: weekly-trades-page (③ 주간 거래기록)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (③ 주간 거래기록, 차트 색상은 동일 시맨틱 팔레트)
- `/docs/ARCHITECTURE.md` (Recharts)
- `/frontend/src/components/ui/`, `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/frontend/src/app/daily/page.tsx` (step 2 — 패턴 일관성)

## 작업

주간 거래기록 페이지(`frontend/src/app/weekly/page.tsx`). **SDD → TDD**: `specs/weekly_trades_page.md` → 스모크 테스트(Red) → 구현.

요소 (UI_GUIDE ③):
- **7일 캔들차트 + 누적 손익 라인 오버레이**: Recharts로 캔들(또는 OHLC 표현) + 누적 손익 라인을 한 차트에 오버레이. 색상은 UI_GUIDE 팔레트(상승 녹색/하락 적색, 라인 중립).
- **요일별 승률 히트맵**: 월~일 7칸, 승률에 따라 색 농도. mock 데이터.
- Recharts는 Client Component(`"use client"`).
- 데이터는 `lib/mock.ts` 주간 mock 기본 + api 시도 후 fallback.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/weekly.test.tsx` — 차트 컨테이너 렌더, 히트맵 7칸, 크래시 없음. (Recharts는 jsdom에서 ResponsiveContainer 사이즈 0 이슈가 있으니, 차트 wrapper에 고정 width/height를 주거나 테스트에서 ResponsiveContainer를 mock하여 렌더만 검증하라.)

## 검증 절차

1. AC 실행.
2. 체크리스트: 캔들+누적손익 오버레이? 히트맵 7칸? 차트 색상이 UI_GUIDE 팔레트? AI 슬롭 미사용?
3. `phases/2-frontend/index.json`의 step 3 업데이트.

## 금지사항

- Recharts 테스트에서 ResponsiveContainer 사이즈 0으로 테스트가 깨지게 두지 마라. 이유: jsdom 한계. 고정 크기 또는 mock.
- 보라/인디고 등 UI_GUIDE 금지 색을 차트에 쓰지 마라.
- 새 프리미티브 만들지 마라. 다른 페이지를 건드리지 마라. 기존 테스트를 깨뜨리지 마라.
