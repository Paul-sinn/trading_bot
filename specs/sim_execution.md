# SPEC: sim_execution (주문 전 검증 + 시뮬레이션 실행 경로)

후보(VetoInput + 제안 Decision)를 받아 **두 게이트를 모두 통과할 때만 시뮬레이트 주문**을 만든다.
실브로커·Robinhood·라이브 주문 없음. 이것은 자동매매로 가는 경로의 시뮬 단계다(영구 수동주문 아님).

관련: `agents/dry_run.py`(build_dry_run_decision — RiskGate 최종권 재사용), `algorithms/policy.py`
(evaluate_hard_veto), `agents/risk.py`(check_risk_gate — 전역 kill-switch), `agents/executor.py`
(라이브 경로 — **건드리지 않음**).

CRITICAL (RiskGate 우회 불가): 시뮬 주문은 오직 `SimulatedExecutor.submit()`을 통해서만 생성·기록된다.
submit은 항상 ① 전역 게이트(kill-switch) ② per-candidate hard-veto를 평가하며, 둘 다 통과 + 진입(BUY)일
때만 주문을 만든다. 주문 리스트에 직접 추가하는 공개 경로는 없다. veto된 후보는 어떤 경로로도 시뮬 주문을
만들 수 없다.

CRITICAL (실주문 0 불변): 이 모듈은 OrderProvider·place_order·브로커·MCP를 부르지 않는다.
`real_orders_placed`는 항상 0(property). 시뮬 주문 수가 늘어도 실주문은 0이다.

CRITICAL (fail-closed): 게이트 예외·hard-veto 평가 예외·수량 ≤ 0이면 시뮬 주문을 만들지 않는다(거부).

## 데이터 모델 (frozen)

```python
@dataclass(frozen=True)
class SimulatedOrder:
    symbol: str
    side: str               # "buy" (이번 범위는 진입 시뮬만)
    quantity: int
    note: str = "SIMULATED — no broker / no live order"

@dataclass(frozen=True)
class SimExecutionResult:
    created: bool
    order: SimulatedOrder | None
    veto: VetoResult | None  # 평가됐으면 상세(게이트 이전 거부면 None)
    reason: str
```

## SimulatedExecutor

```python
class SimulatedExecutor:
    def __init__(self, *, global_gate: Callable[[], tuple[bool,str]] = check_risk_gate)
    @property
    def simulated_orders(self) -> tuple[SimulatedOrder, ...]   # 읽기전용 뷰
    @property
    def real_orders_placed(self) -> int                        # 항상 0
    def submit(self, veto_input, raw_decision, quantity) -> SimExecutionResult
```

### `submit(veto_input, raw_decision, quantity)` 순서 (CRITICAL — 우회 불가)
1. hard-veto 평가(`build_dry_run_decision`). 예외 → fail-closed 거부(veto=None).
2. `quantity <= 0` → 거부.
3. 전역 게이트 `global_gate()`. 예외 → fail-closed 거부. 차단 → 거부.
4. `effective_decision is BUY`가 **아니면** 거부:
   - veto 실패면 사유에 veto 사유 명시(진입 차단).
   - veto 통과지만 raw가 BUY 아님(HOLD/SELL) → "진입 아님" 거부(청산은 이 경로 범위 밖).
5. 위를 모두 통과한 경우에만 `SimulatedOrder` 생성·기록 후 `created=True`.

`effective_decision`은 dry_run의 RiskGate 최종권을 그대로 쓴다 = `BUY ⟺ (veto 통과 AND raw BUY)`.
따라서 veto된 후보는 effective가 BUY가 될 수 없어 4단계에서 반드시 거부된다(우회 불가의 핵심).

## 엣지케이스
- veto 통과 + raw BUY + 수량>0 + 게이트 통과 → 시뮬 주문 1건(단 real_orders_placed 여전히 0).
- veto 실패(예: liquidity_ok=False, needs_review override 없음) + raw BUY → 시뮬 주문 없음.
- kill-switch on(전역 게이트 차단) → 시뮬 주문 없음.
- raw SELL/HOLD → 시뮬 주문 없음(진입만 시뮬).

## 비범위 (하지 않음)
- 실브로커/Robinhood/MCP/executor 라이브 경로 수정. 실주문/체결.
- 청산(SELL) 시뮬, 포트폴리오 상태 추적, 손익 계산.
- 전략/scanner/decision 로직 변경(배선에 꼭 필요한 것 외).
