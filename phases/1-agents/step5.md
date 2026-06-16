# Step 5: reporter-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (리포트 에이전트: 일간/주간 성과 집계, AI 코멘트, DB 저장)
- `/docs/ARCHITECTURE.md` (상태 관리: 서버 상태=SQLite/SQLAlchemy)
- `/docs/ADR.md` (ADR-004: 개발 DB SQLite)
- `/agents/base.py`, `/agents/executor.py`, `/specs/executor_agent.md` (`Fill` 모델)
- `/backend/app/core/config.py` (`database_url`)

## 작업

체결 내역(Fill)을 집계해 **일간/주간 성과**를 계산하고, AI 코멘트(mock)를 붙여 **DB(SQLite)에 저장**하는 리포트 에이전트를 구현한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/reporter_agent.md`

- SQLAlchemy 모델 (`backend/app/db/models.py`):
  - `TradeRecord` — symbol, side, quantity, entry_price, exit_price, realized_pnl, ai_memo, created_at.
  - `DailyReport` / `WeeklyReport` — date, total_pnl, win_rate, trade_count, ai_comment.
  - DB 세션/엔진은 `backend/app/db/session.py` (config.database_url 사용, 기본 SQLite).
- 집계 순수 함수:
  - `aggregate_daily(fills: list[Fill]) -> DailyStats`, `aggregate_weekly(...) -> WeeklyStats`.
  - `win_rate`, `total_pnl`, `trade_count` 계산. realized_pnl 부호로 승패 판정.
- `CommentProvider` 인터페이스: `async def comment(stats) -> str`. `MockCommentProvider`(결정론적 템플릿), `ClaudeCommentProvider`(골격, 키 없으면 예외).
- `ReporterAgent(Agent)`:
  - 생성자에 `AgentRegistry`, DB 세션 팩토리, `CommentProvider` 주입.
  - `async def generate_daily(fills) -> DailyReport`: 집계 → 코멘트 → DB 저장 → 반환.
- 엣지케이스: 빈 fills(0건 → win_rate 0, pnl 0), 전부 손실/이익, 분모 0(trade_count 0) 안전.

### Step B. TEST (Red) — `tests/test_reporter_agent.py`

- `aggregate_daily`: 혼합 손익 fills → win_rate/total_pnl/trade_count 기댓값. 빈 fills 안전.
- DB 저장/조회: 인메모리 SQLite(`sqlite:///:memory:`)로 `generate_daily` 후 레코드가 저장되는지.
- `MockCommentProvider` 코멘트가 stats를 반영하는지(결정론적).
- 분모 0(빈 fills) ZeroDivision 없이 처리.

### Step C. 구현 (Green) — `agents/reporter.py` + `backend/app/db/{models,session}.py`

- 집계는 순수 함수. DB I/O는 에이전트/세션 레이어.
- 테스트는 인메모리 SQLite로 격리(실제 파일 DB 오염 금지).

### Step D. 리팩터

집계 헬퍼와 영속화 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_reporter_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 집계가 순수 함수인가? DB I/O가 분리됐는가? (ADR-002/004)
   - 테스트가 인메모리 SQLite로 격리되어 실제 DB를 오염시키지 않는가?
3. `phases/1-agents/index.json`의 step 5를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 실제 Claude API로 코멘트를 생성하지 마라. `MockCommentProvider` 사용.
- 테스트에서 실제 파일 SQLite(`trading_bot.db`)에 쓰지 마라. 이유: 개발 DB 오염. 인메모리 사용.
- 빈 fills에서 win_rate 분모 0을 처리하지 않으면 안 된다. 이유: ZeroDivision.
- 기존 테스트를 깨뜨리지 마라.
