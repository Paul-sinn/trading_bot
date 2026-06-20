# SPEC: phase1_flow (Phase 1 엔드투엔드 dry-run 통합)

안전한 Phase 1 흐름을 **배선만** 한다:
`scanner → decision → position_weight 제안 → hard-veto → simulated order → dry-run report`.
기존 컴포넌트를 조립하는 오케스트레이터다. 새 전략/시그널/사이징을 만들지 않는다. I/O(에이전트
조율)라 agents/에 둔다.

관련: `agents/scanner.py`(Candidate), `agents/decision.py`(DecisionInput/Decision/MockDecisionProvider),
`algorithms/policy.py`(suggest_position_weight, VetoInput, evaluate_hard_veto), `agents/sim_execution.py`
(SimulatedExecutor), `agents/dry_run.py`(build_dry_run_decision/report).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. `real_orders_placed`는 항상 0. 슬리피지/체결 모델
없음 — 시뮬 주문은 SimulatedOrder 레코드(플레이스홀더)뿐.

CRITICAL (RiskGate 우회 불가): 시뮬 주문은 `SimulatedExecutor.submit`을 통해서만 생성된다(hard-veto +
전역 게이트 평가 포함). veto된 후보는 시뮬 주문을 만들 수 없다(effective가 BUY가 못 됨).

CRITICAL: 전략 로직/시그널 튜닝 없음. 스캐너/디시전은 그대로 호출만 한다.

## 데이터 모델 (frozen)

```python
@dataclass(frozen=True)
class CandidateContext:
    """후보별 시장 컨텍스트 — 스캐너가 아직 안 만드는 비-전략 입력(오케스트레이터가 VetoInput 조립).
    has_stop_loss/position_size_ok는 stop_loss_pct>0 / quantity>0 로 추론."""
    stop_loss_pct: float
    per_trade_risk_pct: float
    regime: Regime
    quantity: int
    liquidity_ok: bool = False
    tier_exposure_ok: bool = False
    data_ok: bool = False
    ipo_data_ok: bool = False
    event_risk_checked: bool = False
    technical_confirmation: bool = False
    manual_override: bool = False

@dataclass(frozen=True)
class Phase1Result:
    report: DryRunReport
    simulated_orders: tuple[SimulatedOrder, ...]
    weight_suggestions: dict[str, WeightSuggestion]
    @property
    def real_orders_placed(self) -> int   # 항상 0
```

## 함수

### `async run_phase1_dry_run(*, scanner, decision_provider, policy, account_phase, risk_mode_name, regime_name, compass_state, contexts, report_date, executor=None) -> Phase1Result`
1. `mode = policy.mode(risk_mode_name)`; 없으면 ValueError(설정 오류).
2. `executor = executor or SimulatedExecutor()`.
3. `candidates = await scanner.scan()`.
4. 각 후보:
   - `raw = (await decision_provider.decide(DecisionInput(cand, dict(cand.detail)))).decision`.
   - `tier = policy.universe.get(symbol).primary_tier` (미등록 None).
   - 컨텍스트 없음 → fail-closed VetoInput(weight=inf, stop=0, regime=None)로 vetoed 행 + 주문 없음.
   - `weight_sug = suggest_position_weight(account_phase, tier, mode, ctx.stop_loss_pct, policy.concentration)`.
     구체 비중 없으면(small_only/rejected/None) → `weight = inf`(fail-closed → veto → 주문 없음).
   - `veto_input = VetoInput(...)`(weight, per_trade, stop, regime, evidence 불리언, manual_override).
   - `executor.submit(veto_input, raw, ctx.quantity)` — 게이트 통과 시에만 시뮬 주문.
   - `row = build_dry_run_decision(veto_input, raw, rationale=...)`.
5. `report = build_dry_run_report(...)`; `Phase1Result(report, executor.simulated_orders, suggestions)`.

불변: 시뮬 주문 존재 ⟺ 행 effective_decision == BUY (둘 다 같은 veto_input에서 파생). real_orders_placed=0.

## 엣지케이스
- BULLISH·필터통과 후보 + 깨끗한 컨텍스트 + raw BUY → 시뮬 주문 1건(real 0).
- veto(liquidity_ok=False 등) → 시뮬 주문 없음, effective HOLD, riskgate_vetoes↑.
- Tier5 후보 → weight small_only(None) → fail-closed veto → 시뮬 주문 없음(자동 집중 금지).
- 컨텍스트 없는 후보 → vetoed, 주문 없음.
- 빈 후보 → 빈 리포트(orders 0).

## 비범위
- 슬리피지/체결가 모델, 포트폴리오 상태/손익, 청산(SELL) 시뮬.
- 실주문/브로커/executor 라이브 경로. 전략/시그널/사이징 수치 변경.
- per-candidate 증거(liquidity 등)를 스캐너가 자동 생성(후속 — 지금은 컨텍스트 입력).
