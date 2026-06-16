# Step 1: dashboard-page (① 대시보드/홈)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (특히 ① 대시보드 핵심 요소, 색상·컴포넌트 규칙)
- `/docs/PRD.md` (핵심기능 1: 실시간 포트폴리오 동기화)
- `/frontend/src/components/ui/` (Card/Button/Gauge/Toggle — step 0 프리미티브)
- `/frontend/src/lib/{api,ws,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/backend/app/ws/ticker.py` (WS 티커 메시지 스키마)

step 0의 프리미티브/타입/mock을 그대로 재사용하라. 새 디자인 시스템을 만들지 마라.

## 작업

대시보드 홈 페이지(`frontend/src/app/page.tsx`)를 구현한다. **SDD → TDD**: 먼저 `specs/dashboard_page.md`(표시 요소/데이터/상태)를 쓰고, 스모크 테스트(Red) 후 구현.

요소 (UI_GUIDE ① 기준):
- **포트폴리오 요약 카드**: 총자산, 오늘 손익(시맨틱 색상), 승률. 큰 수치는 `tabular-nums`.
- **실시간 리스크% 게이지**: Gauge 프리미티브, 값에 따라 녹색→주황→적색.
- **봇 ON/OFF 토글**: Toggle 프리미티브. 상태는 백엔드 권위(UI는 반영) — mock 상태로 표현, 후속 연동.
- **실시간 가격 티커**: `lib/ws.ts`로 `/ws/ticker` 구독해 1초 갱신. 백엔드 미가동 시 mock으로 graceful fallback(크래시 금지). Client Component로.

구현 지침:
- Server Component 기본, 티커·토글 등 실시간/인터랙션만 Client Component (`"use client"`).
- 데이터는 `lib/mock.ts` 기본 + `lib/api.ts` 시도 후 실패 시 mock.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/dashboard.test.tsx` — 페이지가 크래시 없이 렌더, 총자산·승률·게이지·토글 요소 존재.

## 검증 절차

1. AC 실행 (build/lint/test 통과).
2. 체크리스트: UI_GUIDE ① 요소를 모두 포함하는가? 시맨틱 색상(손익) 적용? AI 슬롭 안티패턴 미사용? 티커가 백엔드 없이도 graceful?
3. `phases/2-frontend/index.json`의 step 1 업데이트.

## 금지사항

- 새 디자인 토큰/프리미티브를 만들지 마라. step 0 것을 재사용. 이유: 일관성.
- WS 연결 실패 시 페이지가 크래시하게 두지 마라. 이유: 백엔드 미가동 시 빌드/테스트 실패. graceful fallback.
- frontend에서 Robinhood/Claude 직접 호출 금지(CLAUDE.md CRITICAL).
- 다른 페이지(daily/weekly 등)를 건드리지 마라. 기존 테스트를 깨뜨리지 마라.
