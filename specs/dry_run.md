# SPEC: dry_run (검토용 판단 리포트 — 주문 0건)

후보별 알고리즘/LLM 제안 + 헌법 hard-veto를 종합해 **사람이 검토하는 dry-run 판단 리포트**를 조립한다.
형식은 `docs/templates/decision_report_template.md`를 따른다. 에이전트 출력을 조립하는 I/O-adjacent
작업이라 agents/에 둔다. provider/사이징/스캐너 호출은 하지 않는다 — 호출자가 준비한 입력을 조립만 한다.

관련: `algorithms/policy.py`(evaluate_hard_veto, VetoResult), `agents/decision.py`(Decision),
`docs/templates/decision_report_template.md`, `docs/STRATEGY.md §5`.

CRITICAL (불변): **orders_placed는 항상 0.** 이 모듈은 어떤 경로로도 주문/브로커/실행 코드를 부르지
않는다. BUY 판단이 나와도 주문은 발생하지 않는다(검토용 리포트). `DryRunReport.orders_placed`는 필드가
아니라 항상 0을 돌려주는 property로 구조적으로 강제한다.

CRITICAL (RiskGate 최종권): hard-veto가 막으면 **effective_decision은 BUY가 될 수 없다**(HOLD로 강등).
알고리즘/LLM의 BUY가 veto를 덮어쓰지 못한다(ADR-003/005). veto는 진입 게이트 — SELL(청산)/HOLD는
강등하지 않는다(청산은 막지 않는다). 즉 `raw BUY AND veto 실패 → effective HOLD`, 그 외 → raw 유지.

## 데이터 모델 (frozen)

```python
@dataclass(frozen=True)
class DryRunDecision:
    symbol: str
    tier: str | None          # universe primary_tier (미등록 None)
    status: str | None        # approved/watch/needs_review/... (미등록 None)
    veto: VetoResult          # hard-veto 상세(사유 전부 + risk_check)
    raw_decision: Decision    # 알고리즘/LLM 제안
    effective_decision: Decision  # veto 반영 최종(BUY는 veto면 HOLD)
    position_weight: float
    account_loss_pct: float
    rationale: str

@dataclass(frozen=True)
class DryRunReport:
    report_date: str
    account_phase: str        # "1".."4"
    risk_mode: str            # "B"/"C"
    regime: str               # regime 이름
    compass_state: str        # strong/mixed/weak
    decisions: tuple[DryRunDecision, ...]
    mdd_hard_stop_pct: float = 0.20   # 불변
    no_return_guarantee: bool = True
    @property
    def orders_placed(self) -> int     # 항상 0
    @property
    def riskgate_vetoes(self) -> int    # veto된 후보 수
    @property
    def review_buys(self) -> tuple[str, ...]  # effective BUY 심볼(사람 검토용)
```

## 함수

### `build_dry_run_decision(veto_input, raw_decision, *, rationale="") -> DryRunDecision`
- `veto = evaluate_hard_veto(veto_input)`.
- `effective = HOLD if (raw_decision is BUY and not veto.passed) else raw_decision`.
- tier/status는 `veto_input.universe`에서 조회. account_loss_pct는 `veto.risk_check.account_loss_pct`.
- rationale: 주어진 근거 + veto면 사유 요약 덧붙임.

### `build_dry_run_report(*, report_date, account_phase, risk_mode, regime, compass_state, decisions) -> DryRunReport`
- 헤더 + DryRunDecision 튜플 조립. orders_placed는 property로 0 고정.

### `format_dry_run_report(report) -> str`
- 사람이 읽는 텍스트(템플릿 형식). 푸터에 `orders_placed: 0`, `riskgate_vetoes: n`, `mdd_hard_stop: 0.20`,
  `no_return_guarantee: true`, "자동 라이브 진입 없음" 명시. "DRY-RUN" 문자열 포함.

## 엣지케이스
- decisions 비어있음 → 빈 리포트(orders_placed 0, vetoes 0).
- 미등록 심볼 → tier/status None, veto는 적격 실패로 막힘 → effective HOLD.
- raw BUY + veto 통과 → effective BUY(단 orders_placed 여전히 0).
- raw SELL/HOLD → veto 무관하게 유지(청산 강등 안 함).

## 비범위 (하지 않음)
- scanner/decision provider/sizing 실제 호출(호출자/후속 wiring). 실주문·브로커·executor·live.
- hard-veto를 check_risk_gate에 라이브 배선.
- 전략 시그널/사이징 수치 변경.
