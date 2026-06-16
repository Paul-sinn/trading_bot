# SPEC: weekly_trades_page (③ 주간 거래기록)

Phase 2-frontend Step 3. 프론트엔드 ③ 주간 거래기록(`frontend/src/app/weekly/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 — 거래기록), UI_GUIDE(③ 주간 거래기록, 차트 색상은 동일 시맨틱 팔레트),
ARCHITECTURE(Recharts, 백엔드 SSOT — 프론트는 REST/WS만 호출), ADR-001(프론트/백 분리).

CRITICAL: 프론트는 backend(REST)만 호출한다. Robinhood/Claude를 직접 호출하지 않는다.
CRITICAL: 백엔드 미가동 시에도 페이지가 크래시 없이 렌더되어야 한다(graceful fallback).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/타입/mock)을 재사용한다.

## 표시 요소 (UI_GUIDE ③ 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| 7일 캔들차트 + 누적 손익 라인 오버레이 | OHLC 캔들 + 누적 손익 라인을 한 차트에 오버레이 | `WeeklyBar[]` |
| 요일별 승률 히트맵 | 월~일 7칸, 승률에 따라 색 농도 | `DayWinRate[]` |

### 7일 캔들차트 + 누적 손익 오버레이
- Recharts `ComposedChart`. **Recharts는 Client Component**(`"use client"`)로 분리한다.
- 캔들: 심지(low~high) + 몸통(open~close 범위 Bar). 상승(close≥open) → #22c55e, 하락 → #ef4444.
- 누적 손익: `Line`(중립색)으로 오버레이. 가격 축(좌) / 누적 손익 축(우) 2개 Y축 분리.
- 색상은 UI_GUIDE 시맨틱 팔레트만 사용한다(상승 녹색 / 하락 적색 / 라인·축·그리드 중립).
  보라·인디고 등 금지색·글로우·그라데이션을 쓰지 않는다.
- jsdom에서 `ResponsiveContainer` 사이즈 0 이슈가 있으므로, 차트 wrapper에 고정 높이를 주고
  테스트에서 `ResponsiveContainer`를 mock해 렌더만 검증한다.

### 요일별 승률 히트맵
- 월·화·수·목·금·토·일 **7칸**(순서 고정).
- 각 칸은 승률(0~1)에 따라 녹색 농도(opacity)를 달리해 색 농도를 표현한다.
- 승률 퍼센트 숫자를 병기한다(`tabular-nums`).

## 데이터 로딩 규칙
- 주간 데이터: `getWeekly()`(REST) 시도 → `null`이면 `mockWeekly` fallback.
- 페이지는 Server Component에서 초기 데이터 로드(인터랙션 없음). 차트만 Client Component.

## 타입
- `WeeklyBar`: `date, open, high, low, close, cumulative_pnl`.
- `DayWinRate`: `day("월"~"일"), win_rate(0~1)`.
- `WeeklyReport`: `{ bars: WeeklyBar[]; win_rates: DayWinRate[] }`.

## 엣지케이스
- backend down → REST `null` → `mockWeekly`. 페이지 크래시 없음.
- jsdom 차트 렌더 → `ResponsiveContainer` mock으로 사이즈 0 크래시 방지.
