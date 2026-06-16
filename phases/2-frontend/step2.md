# Step 2: daily-trades-page (② 일간 거래기록)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (② 일간 거래기록, 데이터 테이블 규칙: tabular-nums, 손익 컬럼만 시맨틱 색상)
- `/frontend/src/components/ui/`, `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/agents/reporter.py`, `/backend/app/db/models.py` (TradeRecord 필드 — 표 컬럼과 동기화)
- `/frontend/src/app/page.tsx` (step 1 — 패턴 일관성 참고)

## 작업

일간 거래기록 페이지(`frontend/src/app/daily/page.tsx`). **SDD → TDD**: `specs/daily_trades_page.md` → 스모크 테스트(Red) → 구현.

요소 (UI_GUIDE ②):
- **오늘 체결 내역 테이블**: 컬럼 = 티커, 진입가, 청산가, 실현손익, AI 메모.
  - 헤더 `text-neutral-500 text-xs uppercase`, 행 구분선 `border-neutral-800`.
  - 숫자 컬럼 `tabular-nums text-right`. **실현손익 컬럼만** 시맨틱 색상(이익 녹색/손실 적색).
- 데이터는 `lib/mock.ts`의 오늘 거래 mock 기본, `lib/api.ts`로 실데이터 시도 후 실패 시 mock.
- 빈 상태(거래 0건) 처리: "오늘 체결 없음" 빈 상태 UI.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/daily.test.tsx` — 테이블 헤더 5개 컬럼 렌더, mock 거래 행 표시, 손익 색상 클래스, 빈 상태.

## 검증 절차

1. AC 실행.
2. 체크리스트: 5개 컬럼 정확? 손익만 시맨틱 색상? tabular-nums? 빈 상태 처리?
3. `phases/2-frontend/index.json`의 step 2 업데이트.

## 금지사항

- 모든 숫자 컬럼에 색을 입히지 마라. 손익 컬럼만. 이유: UI_GUIDE 규칙.
- 새 프리미티브 만들지 마라(step 0 재사용). 다른 페이지를 건드리지 마라.
- 기존 테스트를 깨뜨리지 마라.
