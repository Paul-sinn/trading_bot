# SPEC: dashboard_page (① 대시보드 / 홈)

Phase 2-frontend Step 1. 프론트엔드 ① 대시보드 홈(`frontend/src/app/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 1 — 실시간 포트폴리오 동기화), UI_GUIDE(① 대시보드 핵심 요소,
색상·컴포넌트 규칙), ARCHITECTURE(백엔드 SSOT, 프론트는 REST/WS만 호출),
ADR-001(프론트/백 분리).

CRITICAL: 프론트는 backend(REST/WS)만 호출한다. Robinhood/Claude를 직접 호출하지 않는다.
CRITICAL: 백엔드 미가동 시에도 페이지가 크래시 없이 렌더되어야 한다(graceful fallback).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/Button/Gauge/Toggle, 타입, mock)을
재사용한다.

## 표시 요소 (UI_GUIDE ① 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| 포트폴리오 요약 카드 | 총자산 / 오늘 손익 / 승률. 큰 수치는 `tabular-nums`. | `Portfolio` + `Trade[]` |
| 실시간 리스크% 게이지 | `Gauge` 프리미티브. 값에 따라 녹색→주황→적색. | 포지션 노출 비율(파생) |
| 봇 ON/OFF 토글 | `Toggle` 프리미티브. 상태 권위는 backend, UI는 반영만(현재 mock). | mock 상태 |
| 실시간 가격 티커 | `/ws/ticker` 1초 갱신. 백엔드 미가동 시 mock fallback. Client Component. | WS + mock |

### 포트폴리오 요약
- **총자산**: `Portfolio.total_equity`. `$` 통화, `tabular-nums`.
- **오늘 손익**: `Portfolio.day_pnl`. 양수 → 상승색(#22c55e), 음수 → 하락색(#ef4444), 0 → 중립.
  부호(+/−)와 `$` 표기.
- **승률**: 청산된 `Trade`(`exit_price != null`) 중 `realized_pnl > 0` 비율(%).
  청산 거래 0건 → `0.0%`.

### 리스크% 게이지
- 값 = 포지션 노출 비율 = `(total_equity − cash) / total_equity × 100`. 0 분모 → 0.
- `Gauge`가 ≥80 적색, ≥50 주황, 그 외 녹색으로 색을 정한다(step 0 규칙).

### 봇 ON/OFF 토글
- 초기 상태는 mock(예: ON). 클릭 시 로컬 UI 상태만 토글(낙관적 반영).
- 실제 backend 동기화는 후속 step. 이 step에서는 backend 호출 없이 로컬 상태만.

### 실시간 가격 티커
- 마운트 시 `subscribeTicker(symbols, onMessage)`로 `/ws/ticker` 구독, 언마운트 시 해제.
- 초기/실패 시 `mockTicker`(lib/mock.ts)로 표시 → 백엔드 없이도 렌더.
- WS 생성/연결 실패가 페이지를 크래시시키지 않는다(try/catch graceful).
- 심볼별 가격은 `tabular-nums`. 직전 대비 변동은 표시하지 않아도 됨(가격만).

## 데이터 로딩 규칙
- 포트폴리오: `getPortfolio()`(REST) 시도 → `null`이면 `mockPortfolio` fallback.
- Server Component에서 초기 데이터 로드, 실시간/인터랙션(티커·토글)만 Client Component.

## 엣지케이스
- backend down → REST `null` → mock. WS 실패 → mock 티커 유지. 페이지 크래시 없음.
- `day_pnl == 0` → 중립색.
- 청산 거래 0건 → 승률 `0.0%`(0 나눗셈 방지).
- `total_equity == 0` → 리스크 게이지 0%.

## 검증
- `cd frontend && npm run build && npm run lint && npm test` 통과.
- `frontend/src/__tests__/dashboard.test.tsx`: 크래시 없이 렌더, 총자산·승률·게이지·토글 존재.
