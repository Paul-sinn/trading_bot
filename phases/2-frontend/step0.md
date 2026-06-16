# Step 0: frontend-setup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md` (frontend/src 구조, 통신 규약: REST 초기로드 + WebSocket push)
- `/docs/UI_GUIDE.md` (디자인 토큰·색상·컴포넌트·AI 슬롭 안티패턴 — 반드시 준수)
- `/docs/PRD.md` (6개 페이지 정의)
- `/backend/app/services/portfolio.py` (Portfolio/Position 타입 — frontend 타입과 동기화)
- `/backend/app/ws/ticker.py` (WS 메시지 스키마)

## 작업

`frontend/`에 Next.js 14 (App Router) 기반을 스캐폴드한다. 이 step은 **공통 토대만** — 개별 페이지는 이후 step.

### 작업 내용

1. **Next.js 14 + TypeScript + Tailwind** 초기화 (`frontend/`).
   - `package.json` 스크립트: `dev`, `build`, `lint`, `test`.
   - **주의: `create-next-app`의 대화형 프롬프트를 띄우지 마라.** 비대화형으로 설치하거나(`--ts --tailwind --app --eslint --no-src-dir=false --import-alias "@/*" --use-npm --yes`) `package.json`/설정 파일을 직접 작성하라. App Router + `src/` 디렉토리 사용.
   - Next.js 14.x 고정(15 아님 — ADR/CLAUDE.md 스택).

2. **Recharts** 설치.

3. **디자인 토큰** — `tailwind.config` + `globals.css`에 UI_GUIDE.md 색상 반영:
   - 배경 #0a0a0a/#141414/#1a1a1a, 시맨틱 상승 #22c55e·하락 #ef4444·경고 #f59e0b·중립 #525252.
   - 다크모드 고정. CRITICAL: UI_GUIDE.md의 "AI 슬롭 안티패턴"을 위반하지 마라 (glass blur, gradient-text, 보라색 브랜드, gradient orb, 균일 rounded-2xl 금지).

4. **공통 UI 프리미티브** — `frontend/src/components/ui/`:
   - shadcn 대화형 init을 쓰지 말고, UI_GUIDE.md 클래스로 `Card`, `Button`(Primary/Buy/Danger/Text), `Gauge`(리스크/진행 바), `Toggle`(봇 ON/OFF), `Slider`를 직접 작성. 둥근 모서리·간격은 UI_GUIDE 규칙 준수.

5. **레이아웃 + 사이드 내비** — `frontend/src/app/layout.tsx` + `components/Nav.tsx`:
   - 6개 페이지 라우트로 가는 좌측 내비: `/`(대시보드), `/daily`, `/weekly`, `/direction`, `/goals`, `/profile`.
   - 좌측 정렬, 데이터 밀도 우선 레이아웃.

6. **타입 + API/WS 클라이언트** — `frontend/src/types/index.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/ws.ts`:
   - 백엔드 스키마와 일치하는 타입(`Portfolio`, `Position`, `TickerMessage` 등).
   - `api.ts`: backend REST fetch 래퍼(base URL 환경변수, 실패 시 graceful). `ws.ts`: WebSocket 구독 헬퍼.

7. **결정론적 mock 데이터** — `frontend/src/lib/mock.ts`:
   - 백엔드 없이도 페이지가 렌더되도록 mock Portfolio/거래/시황/목표 데이터. 페이지는 기본 mock을 쓰고, 후속에서 실 API로 교체 가능하게.

8. **테스트 셋업 (TDD)** — Vitest + React Testing Library:
   - `npm test` 동작. `frontend/src/__tests__/setup.smoke.test.tsx`: Button/Card 프리미티브가 크래시 없이 렌더되고 UI_GUIDE 클래스가 적용되는지 스모크 테스트.

## Acceptance Criteria

```bash
cd frontend && npm install
npm run build
npm run lint
npm test
```

## 검증 절차

1. 위 AC 커맨드를 frontend/에서 실행한다. build/lint/test 모두 통과해야 한다.
2. 아키텍처 체크리스트:
   - ARCHITECTURE.md의 frontend/src 구조(app/components/lib/types)를 따르는가?
   - UI_GUIDE.md 색상·컴포넌트 규칙을 따르고, AI 슬롭 안티패턴을 위반하지 않았는가?
   - Next.js 14.x, App Router인가?
3. `phases/2-frontend/index.json`의 step 0을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- `create-next-app`/`shadcn init`의 대화형 프롬프트를 띄우지 마라. 이유: 무인 실행이 멈춘다. 비대화형/직접 작성하라.
- UI_GUIDE.md의 AI 슬롭 안티패턴(glass blur, gradient-text, 보라 브랜드색, gradient orb 등)을 쓰지 마라. 이유: 명시적 금지.
- 개별 페이지(대시보드 등)의 실제 콘텐츠를 구현하지 마라. 이유: 이후 step 범위. 라우트 placeholder까지만.
- backend를 직접 import하거나 frontend에서 Robinhood/Claude를 직접 호출하지 마라. 이유: CLAUDE.md CRITICAL. backend REST/WS만.
- 기존 테스트(Python 189개)를 깨뜨리지 마라.
