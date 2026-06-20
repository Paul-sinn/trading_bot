# SPEC: sim_exit (시뮬 포지션 청산)

다일 dry-run 포트폴리오의 보유 포지션을 시뮬 매도(청산)한다. 청산 조건을 평가하고 부분/전량 매도를
`SimulatedPortfolio.apply_sell_fill`로 적용한다.

관련: `agents/sim_portfolio.py`(apply_sell_fill, TradeRecord), `agents/multiday.py`(일별 청산 배선).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음. 시뮬 청산만.

CRITICAL (fail-closed): 가격 결측/무효(None/NaN/≤0)면 청산하지 않고 data_missing 표시(미상가 매도 금지).

## 청산 사유 (ExitReason)
- `stop_loss_hit` : price ≤ stop_price
- `trailing_stop_hit` : price ≤ trailing_high × (1 − trail_pct)
- `time_stop` : hold_days ≥ max_hold_days
- `manual_sim_exit` : 명시적 청산
우선순위: manual > stop_loss > trailing > time.

## 데이터 모델 (frozen)
```python
class ExitReason(str, Enum): STOP_LOSS_HIT/TRAILING_STOP_HIT/TIME_STOP/MANUAL_SIM_EXIT

@dataclass(frozen=True)
class ExitParams:
    stop_price: float | None = None
    trailing_high: float | None = None
    trail_pct: float | None = None
    hold_days: int | None = None
    max_hold_days: int | None = None
    manual_exit: bool = False
    exit_shares: int | None = None   # None=전량, 아니면 부분

@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: ExitReason | None
    shares: int
    data_missing: bool = False

@dataclass(frozen=True)
class ExitResult:
    exited: bool
    reason: ExitReason | None
    shares: int
    realized_pnl: float
    data_missing: bool
    trade: TradeRecord | None
    note: str
```

## 함수
### `evaluate_exit(*, price, shares_held, params) -> ExitDecision` (순수)
- shares_held≤0 → 청산 없음. price None/NaN/≤0 → data_missing(청산 없음, fail-closed).
- 우선순위대로 첫 충족 사유 채택. 매도수량 = `exit_shares`(있으면 min(shares_held)) 아니면 전량.

### `apply_exit(portfolio, symbol, *, price, params) -> ExitResult`
- 포지션 없음 → exited False. evaluate_exit이 data_missing이면 청산 안 함(표시). 충족 시
  `portfolio.apply_sell_fill(symbol, shares, price, exit_reason=...)` 호출.
- 부분 청산 시 잔여 포지션의 평단은 그대로 유지(매도는 평단 불변). 실현 PnL/현금/매매로그 갱신.
  잔여 주식의 미실현 PnL은 이후 mark-to-market 스냅샷이 종가로 반영(보존).

## 자동 trailing-high 추적
- `SimulatedPosition.trailing_high`: 진입가에서 시작, 일별 종가로 **단조 증가**(절대 하락 안 함).
- `SimulatedPortfolio.update_trailing_highs(prices) -> 결측심볼 tuple`: 가격 결측/무효(없음/NaN/≤0)면
  갱신 안 하고 해당 심볼 반환(fail-closed).
- `apply_exit`은 `trail_pct`만 주어지고 명시 `trailing_high`가 없으면 포지션의 추적 `trailing_high`를 쓴다.

## multiday 통합
- `DayInput.exits: dict[symbol, ExitParams]`. 매일 **① update_trailing_highs(종가) → ② entry 흐름 전 청산
  평가·적용** 순서(트레일링 스탑이 그날 갱신된 고점을 쓰고, 청산이 현금을 풀어 신규 진입에 쓰이게).
  `MultiDayResult.day_exits`로 일별 청산 결과 노출.

## 테스트
- 스탑/트레일 청산, 부분/전량 청산의 현금·실현PnL, 잔여 평단 유지, 가격 결측 안전, real orders 0.

## 비범위
- 실브로커/LLM/이벤트 캘린더, 숏/마진, 전략 청산 규칙 튜닝(헌장 §7 레이어는 백테스트 도메인), 시그널 변경.
