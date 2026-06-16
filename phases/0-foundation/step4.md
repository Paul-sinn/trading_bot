# Step 4: mcp-portfolio

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md` (데이터 흐름: Robinhood MCP → backend → WS → UI)
- `/docs/ADR.md` (ADR-005: Claude=최종게이트 / 외부의존 backend 격리)
- `/backend/app/core/config.py`, `/backend/app/main.py`, `/backend/app/ws/ticker.py` (이전 step 산출물)

## 작업

Robinhood MCP를 **mock 인터페이스**로 추상화하고 포트폴리오 fetch 경로를 만든다.
실제 MCP/키 연동은 후속 phase에서 처리한다. 이 step의 목적은 인터페이스 계약과 데이터 모델을 TDD로 고정하는 것이다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/mcp_portfolio.md`

- 포트폴리오 데이터 모델 정의 (Pydantic):
  - `Position(symbol: str, quantity: float, avg_buy_price: float, current_price: float)`
  - `Portfolio(total_equity: float, cash: float, positions: list[Position], day_pnl: float)`
- `PortfolioProvider` 인터페이스: `async def get_portfolio() -> Portfolio`.
- 두 구현: `MockPortfolioProvider`(결정론적 고정 데이터), `RobinhoodPortfolioProvider`(실제 MCP 호출 — 이 step에서는 **시그니처/골격만**, 키 없으면 `NotImplementedError` 또는 명확한 예외).
- 엣지케이스: 빈 포지션, 음수 day_pnl, current_price 누락, MCP 호출 실패 시 처리.
- `GET /api/portfolio` REST 엔드포인트 → `Portfolio` JSON.

### Step B. TEST (Red) — `tests/test_mcp_portfolio.py`

- `MockPortfolioProvider.get_portfolio()`가 spec대로 `Portfolio`를 반환.
- `Portfolio.day_pnl` 계산/필드 검증, 빈 포지션 케이스.
- `GET /api/portfolio`가 mock provider를 주입했을 때 200 + 스키마 일치.
- (실제 provider는 키 없이 호출 시 명확한 예외를 던지는지 확인.)

### Step C. 구현 (Green)

- `backend/app/services/portfolio.py` (services 디렉토리 생성 가능) — `PortfolioProvider`, `MockPortfolioProvider`, `RobinhoodPortfolioProvider` 골격.
- `backend/app/api/portfolio.py` — `GET /api/portfolio` 라우터. provider는 의존성 주입(기본 Mock).
- `backend/app/main.py`에 라우터 등록.
- CRITICAL: provider 선택은 설정(`config.Settings`)으로 분기. 키가 없으면 Mock을 쓰고, 실제 주문/조회를 시도하지 않는다. 이유: 키 부재 시 안전 기본값.

### Step D. 리팩터

provider 주입 구조 정리, 타입 일관성 확인.

## Acceptance Criteria

```bash
pytest tests/test_mcp_portfolio.py -v
python -c "from backend.app.services.portfolio import MockPortfolioProvider; import asyncio; print(asyncio.run(MockPortfolioProvider().get_portfolio()).total_equity)"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - MCP/외부 호출이 backend service 레이어에만 격리되었는가?
   - 키 부재 시 Mock으로 안전하게 fallback하는가 (실거래 시도 없음)?
   - Pydantic 모델이 spec과 일치하는가?
3. `phases/0-foundation/index.json`의 step 4를 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`
   - 실제 Robinhood 키/MCP 연동이 필요해 더 못 나아가면 → 그 부분만 후속 phase로 남기고, mock 경로가 완성되면 `"completed"`로 둔다. (이 step은 mock까지가 범위다.)

## 금지사항

- 실제 Robinhood MCP를 호출하거나 실제 주문/조회를 시도하지 마라. 이유: 키가 없고, 잘못하면 실거래가 나간다. Mock만 구현한다.
- `RobinhoodPortfolioProvider`에 실제 인증 로직을 채워 넣지 마라. 골격 + 명확한 예외까지만. 이유: 키/인증은 후속 phase의 blocked 항목.
- SPEC/TEST 없이 구현부터 하지 마라. 이유: ADR-006 위반.
- 기존 테스트를 깨뜨리지 마라.
