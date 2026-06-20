# SPEC: sim_portfolio (시뮬레이션 포트폴리오 상태 추적)

시뮬 체결(SimulatedFill) 후 시뮬 현금·포지션·노출·실현/미실현 PnL·매매로그를 추적한다. 불가능한 시뮬
주문(현금 부족, 포지션 한도, 티어 노출 한도 초과)을 막는다.

관련: `agents/fill.py`(SimulatedFill), `agents/sim_execution.py`(SimulatedExecutor 연결),
`algorithms/policy.py`(tier).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음.

CRITICAL: 시뮬 체결만 포트폴리오 상태를 갱신한다. 가드 위반(불가능한 주문)이면 상태를 바꾸지 않고
거부한다(원자적 — 검증 후 커밋). veto된 후보는 체결이 없으므로 포트폴리오에 영향이 없다.

## 데이터 모델 (frozen)
```python
@dataclass(frozen=True)
class SimulatedPosition:
    symbol: str
    shares: int
    avg_entry_price: float
    tier: str | None = None
    @property cost_basis -> shares × avg_entry_price
    def market_value(price) -> shares × price
    def unrealized_pnl(price) -> (price − avg_entry_price) × shares

@dataclass(frozen=True)
class TradeRecord:
    symbol: str; side: str; shares: int; price: float; notional: float
    cash_after: float; realized_pnl: float; note: str

@dataclass(frozen=True)
class PortfolioGuardConfig:
    max_position_pct: float | None = None        # 단일 포지션 시가/equity 상한
    tier_exposure_caps: dict[str, float] | None = None  # tier -> equity 대비 상한
    allow_add_to_position: bool = True

@dataclass(frozen=True)
class ApplyResult:
    applied: bool; reason: str; trade: TradeRecord | None
```

## SimulatedPortfolio
```python
SimulatedPortfolio(starting_cash, *, guards=None)
  .starting_cash, .cash, .positions(dict view), .realized_pnl, .trade_log(tuple)
  .real_orders_placed -> 0
  .total_exposure(prices=None) -> Σ 포지션 시가(없으면 cost_basis)
  .equity(prices=None) -> cash + total_exposure
  .unrealized_pnl(prices) -> Σ (price−avg)×shares
  .apply_buy_fill(fill, *, tier=None) -> ApplyResult
  .apply_sell_fill(symbol, shares, price) -> ApplyResult   # 실현 PnL(미배선, 완전성용)
```

### `apply_buy_fill` 가드(순서 — 위반 시 거부, 상태 불변)
1. 현금 부족: `filled_notional > cash` → 거부.
2. 중복 추가 금지: 기존 포지션 있고 `allow_add_to_position=False` → 거부.
3. 단일 포지션 한도: 추가 후 `position_cost / equity_after > max_position_pct` → 거부.
4. 티어 노출 한도: tier 캡 정의돼 있고 추가 후 `tier_cost / equity_after > cap` → 거부.
통과 시: `cash -= notional`, 포지션 생성/평단갱신(`new_avg = (기존cost + notional)/new_shares`),
매매로그 추가. equity/노출은 라이브 가격 없으면 cost_basis(진입) 기준(시뮬 보수).

## SimulatedExecutor 연결
- `SimulatedExecutor(..., portfolio=None)`. 성공 분기에서 fill 생성 후, portfolio 있으면
  `apply_buy_fill(fill, tier)` 호출. 거부되면 **주문/체결을 기록하지 않고 submit도 거부**(불가능 주문 방지).
- real_orders_placed=0 불변.

## 테스트
- 매수 체결 → 현금 감소 / 포지션 생성·평단갱신. 현금부족 → 주문 거부. 노출 계산. vetoed → 포트폴리오 불변.
  real_orders=0. 포지션/티어 한도 초과 거부. 실현 PnL(매도).

## 비범위
- 부분체결/숏/마진, 실브로커/LLM/이벤트 캘린더, 청산 자동 배선(sim_execution은 매수만), 전략/시그널 변경.
