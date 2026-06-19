# 프로젝트: Custom Trading Bot

알고리즘 시그널 + LLM 최종 판단으로 Robinhood 계좌를 리스크 한도 내에서 자동매매하는 봇.
폴리글랏 모노레포 (frontend / backend / agents / algorithms). 상세는 `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md` 참조.

## 기술 스택
- Frontend: Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui, Recharts, WebSocket
- Backend: FastAPI, WebSocket, SQLAlchemy, SQLite(개발)/PostgreSQL(프로덕션), Redis
- AI / 거래: OpenAI API (모델은 OPENAI_MODEL 환경변수, 기본 gpt-4o), Robinhood MCP
  - 주의: 초기 설계는 Claude 기준이었으나 실제 LLM 제공자는 OpenAI다(ADR-007). 코드의 `Claude*Provider` 골격은 `OpenAI*Provider`로 마이그레이션한다. 키 없으면 Mock fallback(안전 기본값).
- 알고리즘: Python 3.11, Pandas/NumPy, Kelly Criterion (지표는 TA-lib 없이 pandas/numpy로 직접 계산 — ADR-008)
- 환경: Mac, Node 20 LTS, Python 3.11, Docker, Git

## 아키텍처 규칙
- CRITICAL: 외부 API(Robinhood MCP, OpenAI API)·DB·시크릿 접근은 backend에서만 한다. frontend는 backend의 REST/WebSocket만 호출하며 거래소/LLM을 직접 호출하지 않는다.
- CRITICAL: 모든 자동 주문은 알고리즘 3레이어 → LLM 판단 → 리스크 게이트(kill-switch) 순서를 통과해야 한다. 이 경로를 우회하는 주문 코드를 만들지 마라.
- CRITICAL: 주문 실행 전 PreToolUse hook의 리스크 체크를 비활성화하거나 우회하지 마라. 한도 초과 시 주문은 반드시 차단된다.
- CRITICAL: LLM(OpenAI)은 설명·판단·근거만 생성한다. 리스크 한도·포지션 사이징 등 안전 수치는 알고리즘이 단일 진실이며 LLM이 하드캡을 덮어쓰지 못한다(ADR-003/005).
- CRITICAL: `.env` 등 시크릿(OpenAI/Robinhood 키)을 코드·로그·커밋에 노출하지 마라. `.gitignore`에 포함한다. Claude(이 에이전트)는 `.env`를 읽지 않는다.
- `algorithms/`는 부수효과 없는 순수 함수로 유지한다. I/O(MCP/LLM/DB)는 `agents/`·`backend/`에만 둔다.
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
윈도우에서 이어가는 법 (요약)

  핵심: 모든 작업물이 GitHub에 있으니 clone만 하면 그대로 이어집니다. Norgate가 윈도우
  전용이라 오히려 윈도우가 맞아요.

  1. 윈도우에 설치

  - Git for Windows(Git Bash 포함 — 훅이 bash라 필요), Python 3.11+(PATH 체크), Claude
  Code(세션 이어가려면)
  
  2. clone + 환경 (PowerShell)

  git clone https://github.com/Paul-sinn/trading_bot.git
  cd trading_bot
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  python -m pytest -q          # 367 통과 확인 = 이어가기 OK

  ⚠️  Mac ↔ 윈도우 차이 딱 3개

  ┌────────────┬─────────────────────────────┬───────────────────────────────────┐
  │            │             Mac             │              윈도우               │
  ├────────────┼─────────────────────────────┼───────────────────────────────────┤
  │ 파이썬     │ .venv/bin/python            │ .venv\Scripts\python              │
  │ 경로       │                             │                                   │
  ├────────────┼─────────────────────────────┼───────────────────────────────────┤
  │ 스크립트   │ PYTHONPATH=.                │ $env:PYTHONPATH="."; python       │
  │ 실행       │ .venv/bin/python ...        │ scripts\run_oos.py ...            │
  ├────────────┼─────────────────────────────┼───────────────────────────────────┤
  │ 훅 설치    │ bash                        │ Git Bash에서 동일 (훅은 윈도우    │
  │            │ scripts/install-hooks.sh    │ 경로 인식하게 고쳐둠)             │
  └────────────┴─────────────────────────────┴───────────────────────────────────┘

  3. Norgate 설정

  1. Norgate 구독 + NDU(Norgate Data Updater) 윈도우 앱 설치 → 백그라운드 데이터 엔진
  실행
  2. pip install norgatedata
  3. 두 갈래:
    - CSV 드롭인(쉬움, 추천): NDU에서 상폐종목 포함 export → data/survivorship_free/에
  두고 python scripts\run_oos.py --root data\survivorship_free (이 폴더는 gitignore됨
  — 라이선스상 커밋 금지)
    - SDK 실연동: agents/data_adapter.py의 NorgateProvider 스켈레톤 채우기(지연 import
  구조 준비됨) — 윈도우에서 같이 하면 됩니다

  4. Claude Code로 이 작업 이어가기

  윈도우에서 clone한 폴더에서 Claude Code 실행하면 됩니다. 이 대화 자체는 안 
  따라가지만, 모든 맥락이 코드에 박혀 있어요:
  - phases/5-momentum-strategy/index.json — step0~11 전체 진행 기록(각 step summary
  포함)
  - docs/STRATEGY.md(헌장) + docs/ADR.md + phases/.../step*.md — 결정·설계 전부
  - 윈도우에서 "step11 이어서 Norgate 실연동하자"처럼 말하면 index.json·step11.md 보고
  바로 이어갑니다

  전체 절차는 docs/WINDOWS_SETUP.md에 체크리스트로 정리해뒀습니다.

  준비되면 윈도우에서 데이터 꽂고 run_oos.py 돌린 결과를 같이 분석하시죠. 막히는
  부분(Norgate export 형식 등) 있으면 물어보세요.