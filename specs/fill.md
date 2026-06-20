# SPEC: fill (시뮬레이션 체결 + 슬리피지 모델)

시뮬 주문(SimulatedOrder)을 시뮬 체결(SimulatedFill)로 연결한다. 순수 슬리피지/체결 수학은 fill.py에,
연결(주문→체결, RiskGate 게이트)은 SimulatedExecutor에 둔다.

관련: `agents/sim_execution.py`(SimulatedOrder/Executor), `agents/phase1_flow.py`(통합),
`agents/evidence.py`(reference_price).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 이벤트 캘린더 실연동
없음(기존 event provider 인터페이스 그대로).

CRITICAL (RiskGate 게이트): 시뮬 체결은 시뮬 주문이 생성된 경우(= 모든 게이트 PASS)에만 만들어진다.
체결은 SimulatedExecutor.submit의 성공 분기에서만 생성된다 — veto된 후보는 주문도 체결도 없다.

## 슬리피지 (단순·보수적)
- spread 있으면: `base = max(default_pct, spread_pct/2)` (하프스프레드, 안전 디폴트 하한 — 보수적).
- 없으면: `base = default_pct`(안전 디폴트).
- 유동성: participation = intended_notional/ADV 있으면 `impact = participation_coeff × participation`.
- `slippage_pct = min(base + impact, max_pct)`. 매수는 체결가를 **올린다**(불리).

## 데이터 모델 (frozen)
```python
@dataclass(frozen=True)
class FillContext:
    reference_price: float
    account_cash: float
    spread_pct: float | None = None
    adv: float | None = None
    default_slippage_pct: float = 0.0010

@dataclass(frozen=True)
class SimulatedFill:
    symbol: str
    side: str
    intended_notional: float     # estimated_shares × reference_price
    estimated_shares: int        # = 주문 수량
    reference_price: float
    slippage_pct: float
    fill_price: float            # reference × (1 + slip)  (buy)
    filled_notional: float       # estimated_shares × fill_price
    cash_remaining: float        # account_cash − filled_notional
    note: str = "SIMULATED FILL — no broker / no live order"
```

## 함수
- `estimate_slippage_pct(*, spread_pct=None, participation=None, default_pct=0.0010, participation_coeff=0.10, max_pct=0.05) -> float`.
- `simulate_fill(order, ctx) -> SimulatedFill` (순수). buy → fill_price = ref×(1+slip) > ref.

## SimulatedExecutor 연결
- `submit(..., *, fill_context: FillContext | None = None)`: 성공 분기(주문 생성)에서 fill_context가
  있으면 `simulate_fill`로 체결 생성·기록. 거부 분기는 fill=None.
- `SimExecutionResult.fill: SimulatedFill | None`. `simulated_fills` 읽기전용 뷰. `real_orders_placed`=0.

## phase1_flow 통합
- `CandidateContext.reference_price`(evidence가 entry로 설정). `run_phase1_dry_run(..., account_cash=None)`:
  주문 생성 시 FillContext(reference_price, account_cash) 구성해 submit에 전달. `Phase1Result.simulated_fills`.

## 테스트
- pass 후보 → 체결 생성. vetoed → 체결 없음. 매수 슬리피지가 체결가↑. real_orders_placed=0.

## 비범위
- 부분체결/취소/리밋, 누적 현금/포지션 상태, 청산(SELL) 체결, 실브로커/이벤트 캘린더, 전략/시그널 변경.
