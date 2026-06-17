# SPEC: mcp_portfolio

Robinhood MCP를 추상화한 **포트폴리오 fetch 경로**. 이 step에서는 실제 MCP/키 연동을
하지 않고, 인터페이스 계약과 데이터 모델만 TDD로 고정한다. 실제 연동은 후속 phase.

관련 문서: ARCHITECTURE(데이터 흐름 — Robinhood MCP → backend(Redis 캐시) → WS → UI),
ADR-001(프론트/백 분리 — 외부 의존은 backend 격리), ADR-005(Claude=최종 게이트).

CRITICAL: 외부 API 호출은 backend service 레이어(`backend/app/services/`)에만 격리한다.
키가 없으면 안전 기본값으로 Mock을 사용하고 **실거래/실조회를 시도하지 않는다**.

## 데이터 모델 (Pydantic)

### Position
| 필드 | 타입 | 설명 |
|------|------|------|
| `symbol` | `str` | 티커 심볼. |
| `quantity` | `float` | 보유 수량. |
| `avg_buy_price` | `float` | 평균 매입 단가. |
| `current_price` | `float` | 현재가. |

### Portfolio
| 필드 | 타입 | 설명 |
|------|------|------|
| `total_equity` | `float` | 총자산(현금 + 포지션 평가액). |
| `cash` | `float` | 현금 잔고. |
| `positions` | `list[Position]` | 보유 포지션 목록(빈 리스트 허용). |
| `day_pnl` | `float` | 당일 손익(음수 허용). |

## PortfolioProvider 인터페이스

```python
class PortfolioProvider(Protocol):
    async def get_portfolio(self) -> Portfolio: ...
```

### MockPortfolioProvider
- 결정론적 고정 데이터를 반환한다. 외부 의존성/난수 없음.
- 같은 호출은 항상 같은 `Portfolio`를 돌려준다.
- 최소 1개 포지션을 포함하며, `total_equity == cash + Σ(quantity × current_price)`를 만족.

### RobinhoodPortfolioProvider
- Robinhood는 공개 API 키가 없다 — robinhood-trading **MCP 서버**로 조회한다(`get_portfolio`/`get_equity_positions` 등).
- 실제 MCP 브리지는 **골격만**. 선택적 `mcp_client`를 주입받되 이 단계에서 채우지 않는다.
- `get_portfolio()` 호출 시 명확한 예외(`NotImplementedError`)를 던져 실조회로 새지 않게 한다.
- MCP 실연동은 통합 phase 범위.

## provider 선택 (config 분기)

- `get_portfolio_provider()`는 `config.Settings.robinhood_mcp_enabled`로 분기한다.
  - False(기본) → `MockPortfolioProvider` (안전 기본값, 실조회 없음).
  - True → `RobinhoodPortfolioProvider` (이 단계에서는 호출 시 NotImplementedError).
- 이유: 기본값을 Mock으로 두어 실거래/실조회 시도 없이 안전하게 동작.

## REST 엔드포인트

### GET /api/portfolio
- **입력**: 없음.
- **출력**: `200 OK`, body는 `Portfolio` JSON.
- **provider**: FastAPI 의존성 주입(`get_portfolio_provider`). 테스트는 Mock으로 override.

## 엣지케이스
- **빈 포지션**: `positions == []`. `total_equity == cash`, 크래시 없음.
- **음수 day_pnl**: 손실 상황. 그대로 음수 허용.
- **current_price 누락**: Position은 `current_price` 필수. 누락 시 검증 에러(Pydantic).
- **MCP 미연동**: RobinhoodPortfolioProvider 골격은 호출 시 NotImplementedError(실조회로 새지 않음).

## 비범위 (이 step에서 하지 않음)
- 실제 Robinhood MCP 호출 / 인증 / 실주문 / 실조회.
- DB 영속화, Redis 캐시.
- 주문/설정/리포트 라우트 (후속 step).
