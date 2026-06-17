# 프로젝트: Custom Trading Bot

알고리즘 시그널 + Claude 최종 판단으로 Robinhood 계좌를 리스크 한도 내에서 자동매매하는 봇.
폴리글랏 모노레포 (frontend / backend / agents / algorithms). 상세는 `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md` 참조.

## 기술 스택
- Frontend: Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui, Recharts, WebSocket
- Backend: FastAPI, WebSocket, SQLAlchemy, SQLite(개발)/PostgreSQL(프로덕션), Redis
- AI / 거래: Claude API (claude-sonnet-4-6), Robinhood MCP
- 알고리즘: Python 3.11, Pandas, TA-lib, Kelly Criterion
- 환경: Mac, Node 20 LTS, Python 3.11, Docker, Git

## 아키텍처 규칙
- CRITICAL: 외부 API(Robinhood MCP, Claude API)·DB·시크릿 접근은 backend에서만 한다. frontend는 backend의 REST/WebSocket만 호출하며 거래소/AI를 직접 호출하지 않는다.
- CRITICAL: 모든 자동 주문은 알고리즘 3레이어 → Claude 판단 → 리스크 게이트(kill-switch) 순서를 통과해야 한다. 이 경로를 우회하는 주문 코드를 만들지 마라.
- CRITICAL: 주문 실행 전 PreToolUse hook의 리스크 체크를 비활성화하거나 우회하지 마라. 한도 초과 시 주문은 반드시 차단된다.
- CRITICAL: `.env` 등 시크릿(Robinhood/Claude 키)을 코드·로그·커밋에 노출하지 마라. `.gitignore`에 포함한다.
- `algorithms/`는 부수효과 없는 순수 함수로 유지한다. I/O(MCP/Claude/DB)는 `agents/`·`backend/`에만 둔다.
- 컴포넌트는 `frontend/src/components/`, 타입은 `frontend/src/types/`, 에이전트는 `agents/`, 시그널·필터·사이징은 `algorithms/`에 분리한다.

## 개발 프로세스 (SDD → TDD 강제)
- CRITICAL: 기능 구현은 반드시 다음 순서를 따른다.
  1. SPEC: `specs/기능명.md`에 입력/출력/엣지케이스 정의
  2. TEST(Red): `tests/test_기능명.py` 작성 후 실패 상태로 커밋
  3. 구현(Green): 테스트를 통과시킬 최소 코드만 작성
  4. 리팩터(Refactor): 테스트 유지한 채 정리
- 커밋 메시지는 conventional commits 형식 (feat:, fix:, docs:, refactor:, test:, chore:).
- git hooks: pre-commit(ESLint + Prettier 자동 수정, 실패 시 차단), pre-push(단위테스트 전체 통과 확인, 실패 시 차단).

## 명령어
```
# Frontend (frontend/)
npm run dev        # 개발 서버
npm run build      # 프로덕션 빌드
npm run lint       # ESLint
npm test           # 테스트

# Backend / Python (루트)
uvicorn backend.app.main:app --reload   # 백엔드 서버
pytest             # 전체 테스트
pytest tests/test_기능명.py             # 단일 기능 테스트
```

## 실수 기록 (재발 방지)
- **dev 서버 켜둔 채 `npm run build` 금지**: `npm run dev`가 떠 있는 상태에서 `npm run build`(프로덕션 빌드)를 돌리면 dev 서버의 `frontend/.next` 디렉토리를 프로덕션 산출물로 덮어쓴다. 그러면 dev 서버가 기대하는 CSS 경로(`/_next/static/css/app/layout.css`)가 404가 되어 **스타일이 전부 깨져 보인다**(코드 문제 아님).
  - 원인: dev와 build가 같은 `.next`를 공유. build가 dev 빌드 산출물을 클로버.
  - 복구: `pkill -f "next dev"` → `rm -rf frontend/.next` → `npm run dev` 재시작.
  - 예방: 검증 빌드는 dev 서버를 끄고 하거나, dev 서버는 그대로 두고 빌드 검증은 CI/별도 클린 체크아웃에서 한다. dev 중 build가 필요하면 `.next` 충돌을 인지하고 끝나면 dev 재시작.
