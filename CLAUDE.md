# 프로젝트: Custom Trading Bot

알고리즘 시그널 + LLM 판단으로 주식 계좌를 **리스크 한도 내에서만** 자동매매하는 봇(롱온리 스윙).
폴리글랏 모노레포. 두 축으로 진행 중이다:

- **앱(Phase 0–3 완료)**: Next.js 6페이지 + FastAPI(SSOT) + 3레이어 알고리즘 + LLM 판단 + RiskGate +
  목표기반 AI 개인화(goal planner). 자세히는 `PROJECT_OVERVIEW.md`.
- **전략 R&D(Phase 5, 현재 초점)**: 시계열 모멘텀(추세추종) + SPY 상대강도. Norgate 데이터로 백테스트·전진
  검증·OOS. 모든 산출물은 **report-only(실주문 0)**. 헌장 `docs/STRATEGY.md`가 전략 SSOT다.

> 새 세션은 **`docs/STRATEGY.md`(헌장) → `phases/5-momentum-strategy/index.json`(step별 진행) → `docs/ADR.md`**
> 순으로 읽으면 맥락을 가장 빨리 잡는다. 설계 상세는 `docs/PRD.md`·`docs/ARCHITECTURE.md`.

## 디렉토리
```
algorithms/  매매 계산(시그널/필터/사이징/진입/청산/레짐/유니버스) — 순수 함수, I/O 없음
agents/      백테스트·시뮬·전진검증 로직(decision/decision_outcome/daily_shadow/historical_sim/exit_* 등 53개)
experiments/ 리포트 러너(python -m experiments.<name>) — shadow report·OOS·ablation 등
backend/     FastAPI(app/api·services·ws·db) — 모든 외부 API·DB·시크릿의 단일 관문
frontend/    Next.js 14(App Router) — backend REST/WS만 호출
scripts/     run_sim·run_oos 등 시뮬 CLI + install-hooks.sh
specs/ tests/ 기능별 SDD 스펙 + TDD 테스트
docs/        STRATEGY(헌장)/ADR/PRD/ARCHITECTURE/UI_GUIDE/UNIVERSE_TIERS/{MAC,WINDOWS}_SETUP
phases/      단계별 step 기록(index.json + step*.md) — 진행 맥락의 단일 출처
data/ reports/  Norgate CSV / 시뮬 산출물 — 둘 다 .gitignore(아래 참조)
```

## 기술 스택
- Frontend: Next.js 14(App Router), TypeScript, Tailwind, Recharts, WebSocket. 테스트 vitest.
- Backend: FastAPI, WebSocket, SQLAlchemy, SQLite(개발)/PostgreSQL(프로덕션), Redis.
- AI/거래: OpenAI API(모델은 `OPENAI_MODEL`, 기본 gpt-4o), Robinhood MCP. **키 없으면 Mock fallback(안전 기본값).**
  - LLM 제공자는 OpenAI다(ADR-007). 코드에 `Claude*Provider` 골격이 남아있으면 `OpenAI*Provider`로 마이그레이션.
- 알고리즘: Python 3.11, Pandas/NumPy, Kelly(half, 상한 0.25). 지표는 TA-Lib 없이 직접 계산(ADR-008).
- 환경: Mac(주개발) / Windows(Norgate 데이터 갱신 전용). Node 20 LTS, Python 3.11.

## 아키텍처 규칙 (CRITICAL)
- 외부 API(Robinhood MCP·OpenAI)·DB·시크릿 접근은 **backend에서만**. frontend는 backend REST/WS만 호출하고
  거래소/LLM을 직접 호출하지 않는다(ADR-001).
- 모든 자동 주문은 **알고리즘 3레이어 → LLM 판단 → 리스크 게이트(kill-switch)** 순서를 통과해야 한다.
  이 경로를 우회하는 주문 코드를 만들지 마라.
- 주문 실행 전 PreToolUse hook의 리스크 체크를 비활성화·우회하지 마라. 한도 초과 시 주문은 반드시 차단(fail-closed).
- **LLM은 설명·판단·근거만** 생성한다. 리스크 한도·포지션 사이징 등 안전 수치는 알고리즘이 단일 진실이며 LLM이
  하드캡을 덮어쓰지 못한다(ADR-003/005). `SYSTEM_MAX_RISK_PCT` 절대 한도 초과 불가.
- `.env`·시크릿(OpenAI/Robinhood 키)을 코드·로그·커밋에 노출하지 마라. Claude는 `.env`를 읽지 않는다.
- `algorithms/`는 부수효과 없는 순수 함수로 유지한다. I/O(MCP/LLM/DB)는 `agents/`·`backend/`에만 둔다.

## 전략 R&D 불변식 (CRITICAL — shadow report·백테스트 작업 시)
현재 작업 대부분은 **report-only 전진 검증**이다. 다음을 절대 어기지 마라:
- **실주문 없음**: 브로커/Robinhood/MCP/라이브 주문 없음. LLM 뉴스 API 미연결. `real_orders_placed = 0` 항상.
- **잠긴 베이스라인(변경 금지 — `tests/test_baseline_lock.py`가 못 박음)**: 진입 `next-bar-limit` + buffer
  `0.03`, 손절 `0.15`, 트레일링 `0.20`, 최대보유 `60`일, fractional shares, `weekend_exit_symbols` 기본 빈 집합
  (주말청산은 레버리지 전용 opt-in), next-open 기본, gap guard, winner extension. 90/120은 실험 변형 전용.
- **읽기 전용**: 스캐너/디시전/사이징/RiskGate·진입모델·청산정책·기본 유니버스를 바꾸지 마라. 베이스라인을
  '서술'만 하고(plan 상수) 변경하지 않는다. 원장(reports/*.jsonl)은 ID 멱등 append — 중복 행 금지.
- **검증 전 라이브 금지**(헌장 §3·§10): 생존편향 제거·OOS가 SPY를 위험조정으로 이긴다는 사람 판정 전엔 greenlight
  없음. "수익 보장" 류 표현 금지(헌장 §0.7).

## 개발 프로세스 (SDD → TDD 강제, CRITICAL)
1. **SPEC**: `specs/기능명.md`에 입력/출력/엣지케이스 정의
2. **TEST(Red)**: `tests/test_기능명.py` 작성 후 실패 상태로 커밋
3. **구현(Green)**: 테스트를 통과시킬 최소 코드만
4. **리팩터**: 테스트 유지한 채 정리
- 커밋은 conventional commits(feat/fix/docs/refactor/test/chore). 커밋·푸시는 사용자가 요청할 때만.
- git hooks: pre-commit(ESLint+Prettier, 실패 시 차단), pre-push(전체 단위테스트 통과 확인, 실패 시 차단).
  설치: `bash scripts/install-hooks.sh`.

## 명령어 (루트 기준, Mac)
```bash
# Python / Backend
source .venv/bin/activate
python -m pytest -q                                   # 전체 테스트(현재 backend ~1046 통과)
python -m pytest tests/test_기능명.py                 # 단일 기능
PYTHONPATH=. uvicorn backend.app.main:app --reload    # 백엔드(localhost:8000)
PYTHONPATH=. python -m experiments.daily_shadow_report # 섀도 리포트 재생성(report-only)
PYTHONPATH=. python scripts/run_oos.py --root data/survivorship_free  # OOS 재검증

# Frontend (frontend/)
npm install && npm run dev    # localhost:3000
npm run build                 # 프로덕션 빌드(아래 주의)
npm run lint                  # ESLint
npm test                      # vitest
```
> Windows에서는 `.venv\Scripts\python.exe`, `$env:PYTHONPATH="."` 로 치환(표는 `docs/MAC_SETUP.md`).

## 환경 / 데이터 (.gitignore — clone에 안 들어감)
- `data/`(Norgate CSV: `ndu_export*`), `.env`, `reports/`, `.venv/`, `node_modules/`, `*.db`는 깃에 없다.
- **데이터 갱신은 Windows 전용**(Norgate NDU 윈도우 앱). 맥은 Windows의 `data/`를 복사해 쓰고 개발만 이어간다.
- 맥↔윈도우 이전·이어가기 체크리스트: `docs/MAC_SETUP.md` / `docs/WINDOWS_SETUP.md`.
  맥에 기존 프로젝트가 있으면 새 clone 말고 **`git pull origin main`**.

## 실수 기록 (재발 방지)
- **dev 서버 켜둔 채 `npm run build` 금지**: dev와 build가 같은 `frontend/.next`를 공유 → build가 dev 산출물을
  덮어써 CSS 404로 **스타일이 전부 깨진다**(코드 문제 아님). 복구: `pkill -f "next dev"` → `rm -rf frontend/.next`
  → `npm run dev` 재시작. 빌드 검증은 dev를 끄고 하거나 끝나면 dev 재시작.
```
