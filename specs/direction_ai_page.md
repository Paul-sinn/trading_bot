# SPEC: direction_ai_page (④ 방향성 & AI 분석)

Phase 2-frontend Step 4. 프론트엔드 ④ 방향성 & AI 분석(`frontend/src/app/direction/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 4 — AI 시황 분석, 매일 9시 Claude 시황 요약 + 7일 방향성),
UI_GUIDE(④ 방향성 & AI 분석, 방향성=강세/중립/약세 색), ARCHITECTURE(백엔드 SSOT),
ADR-001(프론트/백 분리), ADR-005(Claude는 backend 최종 게이트).

CRITICAL: 프론트는 backend(REST)만 호출한다. Claude를 직접 호출하지 않는다(결과만 표시).
CRITICAL: 백엔드 미가동 시에도 페이지가 크래시 없이 렌더되어야 한다(graceful fallback).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/타입/mock)을 재사용한다.
"Powered by AI" 배지·보라/인디고 브랜딩 등 AI 슬롭 안티패턴을 쓰지 않는다.

## 표시 요소 (UI_GUIDE ④ 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| Claude 시황 요약 카드 | 매일 9시 생성된 시황 요약 텍스트 + 생성 시각 표시 | `MarketDirection.summary`, `.date` |
| 다음 7일 예상 방향 카드 | 강세/중립/약세 라벨 + 근거 텍스트 | `MarketDirection.label`, `.rationale` |

### 시황 요약 카드
- 생성 시각(`date`)을 보조 라벨로 표시한다(예: "2026-06-16 09:00 생성").
- 요약 본문은 `text-neutral-300`. 장식 없이 텍스트 우선.

### 7일 방향성 카드
- 라벨 3종: `bullish`→"강세", `neutral`→"중립", `bearish`→"약세".
- 라벨 색: 강세 #22c55e(녹색), 중립 #525252/중립색, 약세 #ef4444(적색). 방향성 외 컬러 금지.
- 근거(`rationale`) 텍스트를 라벨 아래 표시한다.

## 데이터 로딩 규칙
- 시황/방향성: `getDirection()`(REST) 시도 → `null`이면 `mockDirection` fallback.
- 페이지는 Server Component에서 초기 데이터 로드(인터랙션 없음).

## 타입
- `DirectionLabel`: `"bullish" | "neutral" | "bearish"`.
- `MarketDirection`: `{ date, summary, label, rationale }`.

## 엣지케이스
- backend down → REST `null` → `mockDirection`. 페이지 크래시 없음.
- 알 수 없는 라벨 → 중립색으로 안전 처리(기본값).
