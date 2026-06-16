# SPEC: daily_trades_page (② 일간 거래기록)

Phase 2-frontend Step 2. 프론트엔드 ② 일간 거래기록(`frontend/src/app/daily/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 — 거래기록), UI_GUIDE(② 일간 거래기록, 데이터 테이블 규칙),
ARCHITECTURE(백엔드 SSOT, 프론트는 REST/WS만 호출), ADR-001(프론트/백 분리).

CRITICAL: 프론트는 backend(REST)만 호출한다. Robinhood/Claude를 직접 호출하지 않는다.
CRITICAL: 백엔드 미가동 시에도 페이지가 크래시 없이 렌더되어야 한다(graceful fallback).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/타입/mock)을 재사용한다.

## 표시 요소 (UI_GUIDE ② 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| 오늘 체결 내역 테이블 | 티커·진입가·청산가·실현손익·AI 메모 5개 컬럼 | `Trade[]` |
| 빈 상태 | 거래 0건 시 "오늘 체결 없음" 안내 | — |

### 체결 내역 테이블
- 컬럼(5개, 순서 고정): **티커 / 진입가 / 청산가 / 실현손익 / AI 메모**.
- 헤더: `text-neutral-500 text-xs uppercase`. 행 구분선 `border-neutral-800`.
- 숫자 컬럼(진입가·청산가·실현손익)은 `tabular-nums text-right`.
- **실현손익 컬럼만** 시맨틱 색상: 이익 → 상승색(#22c55e), 손실 → 하락색(#ef4444), 0 → 중립.
  (`pnlColorClass` 재사용). 다른 숫자 컬럼에는 색을 입히지 않는다(UI_GUIDE 규칙).
- 진입가·청산가·실현손익은 `formatUsd`로 `$` 표기. 청산가가 `null`(미청산)이면 `—` 표시.
- 티커는 `Trade.symbol`, AI 메모는 `Trade.ai_memo`(좌측 정렬 본문).

### 빈 상태
- `Trade[]`가 비면 테이블 대신 "오늘 체결 없음" 안내를 표시한다.

## 데이터 로딩 규칙
- 거래: `getTrades()`(REST) 시도 → `null`이면 `mockTrades` fallback.
- Server Component에서 초기 데이터 로드(인터랙션 없음).

## 엣지케이스
- backend down → REST `null` → mockTrades. 페이지 크래시 없음.
- 청산가 `null`(미청산 포지션) → `—`.
- 거래 0건 → 빈 상태 UI.
