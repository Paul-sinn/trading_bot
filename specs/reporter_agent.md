# SPEC: reporter_agent (리포트 에이전트 — 체결 집계 → 일간/주간 성과 + AI 코멘트 → DB 저장)

리포트 에이전트는 실행 에이전트가 남긴 체결 내역(`Fill`)을 모아 **일간/주간 성과**(총손익·승률·
거래수)를 집계하고, AI 코멘트(이 phase는 mock)를 붙여 **SQLite(SQLAlchemy)** 에 저장한다.

관련 문서: PRD(리포트 = 일간/주간 성과 집계, AI 코멘트, DB 저장),
ARCHITECTURE(상태 관리: 서버 상태 = SQLite/PostgreSQL, SQLAlchemy로 추상화),
ADR-002(집계는 순수 함수, I/O는 에이전트/세션 레이어), ADR-004(개발 DB = SQLite),
`specs/agent_base.md`(Agent·AgentRegistry), `specs/executor_agent.md`(`Fill`).

CRITICAL: 실제 Claude API로 코멘트를 생성하지 않는다. 이 phase는 결정론적 `MockCommentProvider`만
사용한다. `ClaudeCommentProvider`는 골격 + 명확한 예외까지만(실호출 금지, 키/연동은 후속 phase).

CRITICAL: 집계는 **순수 함수**(부수효과·I/O 없음)로 유지한다. DB 저장 등 I/O는 에이전트/세션
레이어에만 둔다(ADR-002/004). 테스트는 **인메모리 SQLite**로 격리해 실제 파일 DB를 오염시키지 않는다.

## 입력 모델 — `Fill` (executor)

집계 입력은 `agents.executor.Fill`이다. 청산 손익을 담기 위해 `Fill`에 선택 필드
`realized_pnl: float = 0.0`(기본 0.0, 후행·비파괴)을 둔다 — 청산(매도) 체결에서 실현 손익이
채워지고, 진입 체결은 0.0이다. 집계는 이 `realized_pnl`의 부호로 승/패를 판정한다.

## 집계 순수 함수

```python
@dataclass(frozen=True)
class DailyStats:
    total_pnl: float
    win_rate: float      # 0~1, trade_count==0이면 0.0 (ZeroDivision 안전)
    trade_count: int

@dataclass(frozen=True)
class WeeklyStats:
    total_pnl: float
    win_rate: float
    trade_count: int

def aggregate_daily(fills: list[Fill]) -> DailyStats: ...
def aggregate_weekly(fills: list[Fill]) -> WeeklyStats: ...
```

- `trade_count = len(fills)`.
- `total_pnl = sum(f.realized_pnl)`.
- `win = realized_pnl > 0`. `win_rate = wins / trade_count` (단, `trade_count == 0`이면 `0.0`).
- 부수효과 없음. 동일 입력 → 동일 출력(결정론).
- 주간 그룹핑(어떤 체결이 어느 주에 속하는가)은 **호출측 책임**이다. 두 함수는 받은 fills를
  그대로 집계한다(대칭).

## 코멘트 provider (외부 의존 주입)

### `CommentProvider`
```python
class CommentProvider(Protocol):
    async def comment(self, stats) -> str: ...
```

### `MockCommentProvider`
- **결정론적** 템플릿. stats(총손익·승률·거래수)를 반영한 문자열을 반환한다.
  난수·외부 호출 없음. 동일 stats → 동일 코멘트.

### `ClaudeCommentProvider`
- Claude 호출 구조는 **주석으로만** 남긴다(골격). 실제 호출 금지.
- 키가 없으면 명확한 예외(`ValueError`), 키가 있어도 실호출하지 않고 `NotImplementedError`.

## SQLAlchemy 모델 — `backend/app/db/models.py`

- `Base`(DeclarativeBase).
- `TradeRecord`: id, symbol, side, quantity, entry_price, exit_price, realized_pnl, ai_memo, created_at.
- `DailyReport`: id, date, total_pnl, win_rate, trade_count, ai_comment, created_at.
- `WeeklyReport`: id, date, total_pnl, win_rate, trade_count, ai_comment, created_at.

## 세션/엔진 — `backend/app/db/session.py`

- `make_engine(database_url=None)` / `make_session_factory(database_url=None, *, create=True)`.
- `database_url`은 기본적으로 `config.Settings().database_url`(기본 SQLite)을 사용한다.
- SQLite는 `check_same_thread=False`. 인메모리(`:memory:`)는 `StaticPool`로 단일 DB 공유.
- `expire_on_commit=False` — 커밋 후 반환 객체 속성 접근 안전.

## ReporterAgent(Agent)

```python
class ReporterAgent(Agent):
    def __init__(self, registry: AgentRegistry,
                 session_factory: Callable[[], Session],
                 comment_provider: CommentProvider, *,
                 name: str = "reporter") -> None: ...
    async def generate_daily(self, fills: list[Fill],
                             report_date: date | None = None) -> DailyReport: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속.
- 생성자에 `AgentRegistry`, **세션 팩토리**, `CommentProvider`를 주입한다.
- `generate_daily(fills)` — 순서: 집계(`aggregate_daily`) → 코멘트(`comment_provider.comment`)
  → `DailyReport` 영속화(세션 add/commit) → 저장된 레코드 반환.
- `tick()` — 루프 1회: 현재 step은 체결 소스 연결 전이므로 no-op. 후속 step에서 배선한다.

## 엣지케이스

- 빈 fills → trade_count 0, total_pnl 0.0, win_rate 0.0(분모 0 안전, ZeroDivision 없음).
- 전부 손실 → win_rate 0.0. 전부 이익 → win_rate 1.0.
- breakeven(realized_pnl == 0) → 승으로 세지 않음(win 은 > 0 만).
- `ClaudeCommentProvider` 키 없이 호출 → `ValueError`.

## 비범위 (이 step에서 하지 않음)

- 실제 Claude 코멘트 생성(주입 Mock provider만 사용).
- 주간 그룹핑/스케줄링(매일 9시 트리거는 후속). 두 집계 함수는 받은 fills만 집계.
- 프론트/REST 노출, WebSocket push.
- TradeRecord 개별 영속화 흐름(모델만 정의; 후속 배선).
