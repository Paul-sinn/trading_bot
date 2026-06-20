"""Phase 1 엔드투엔드 dry-run 통합 — 기존 컴포넌트 배선만.

흐름: scanner → decision → position_weight 제안 → hard-veto(SimulatedExecutor 게이트) →
simulated order → dry-run report. 새 전략/시그널/사이징을 만들지 않는다. 에이전트 조율 I/O라 agents/.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 슬리피지/체결 모델
없음 — 시뮬 주문은 SimulatedOrder 레코드(플레이스홀더)뿐.

CRITICAL (RiskGate 우회 불가): 시뮬 주문은 SimulatedExecutor.submit(hard-veto + 전역 게이트 평가)
을 통해서만 생성된다. veto된 후보는 effective가 BUY가 못 돼 주문이 생기지 않는다.

CRITICAL: 전략 로직/시그널 튜닝 없음. scanner/decision은 그대로 호출만 한다.

spec: specs/phase1_flow.md
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.decision import Decision, DecisionInput
from agents.dry_run import (
    DryRunReport,
    build_dry_run_decision,
    build_dry_run_report,
)
from agents.sim_execution import SimulatedExecutor, SimulatedOrder
from algorithms.policy import (
    Policy,
    VetoInput,
    WeightSuggestion,
    suggest_position_weight,
)
from algorithms.regime import Regime

# 구체 비중을 제안받지 못한 경우(small_only/rejected/None) fail-closed로 쓰는 무효 비중 → veto.
_NO_WEIGHT = float("inf")


@dataclass(frozen=True)
class CandidateContext:
    """후보별 시장 컨텍스트 — 스캐너/데이터에서 파생되는 증거 + 사이징(VetoInput 조립용).

    has_stop_loss/position_size_ok는 stop_loss_pct>0 / quantity>0 로 추론한다. 증거는 직접 채우거나
    agents.evidence.build_candidate_context로 자동 구성한다. technical_confirmation은 trend/volume/
    relative_strength 셋의 AND(빌더가 설정). regime 산출 실패 시 None(fail-closed).
    """

    stop_loss_pct: float
    per_trade_risk_pct: float
    regime: Regime | None
    quantity: int
    # 증거(transparency) — technical_confirmation = 아래 셋의 AND
    trend_confirmed: bool = False
    volume_confirmed: bool = False
    relative_strength_confirmed: bool = False
    liquidity_ok: bool = False
    tier_exposure_ok: bool = False
    data_ok: bool = False
    ipo_data_ok: bool = False
    event_risk_checked: bool = False
    technical_confirmation: bool = False
    manual_override: bool = False


@dataclass(frozen=True)
class Phase1Result:
    """Phase 1 dry-run 결과. real_orders_placed는 항상 0."""

    report: DryRunReport
    simulated_orders: tuple[SimulatedOrder, ...]
    weight_suggestions: dict[str, WeightSuggestion]

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음."""
        return 0


def _veto_input_for(
    symbol: str, mode, universe, weight: float, ctx: CandidateContext
) -> VetoInput:
    """후보 + 컨텍스트 + 제안 비중으로 VetoInput을 조립한다."""
    return VetoInput(
        symbol=symbol,
        mode=mode,
        universe=universe,
        per_trade_risk_pct=ctx.per_trade_risk_pct,
        position_weight=weight,
        stop_loss_pct=ctx.stop_loss_pct,
        regime=ctx.regime,
        has_stop_loss=ctx.stop_loss_pct > 0,
        position_size_ok=ctx.quantity > 0,
        liquidity_ok=ctx.liquidity_ok,
        tier_exposure_ok=ctx.tier_exposure_ok,
        data_ok=ctx.data_ok,
        ipo_data_ok=ctx.ipo_data_ok,
        event_risk_checked=ctx.event_risk_checked,
        technical_confirmation=ctx.technical_confirmation,
        manual_override=ctx.manual_override,
    )


def _no_context_veto_input(symbol: str, mode, universe) -> VetoInput:
    """컨텍스트 없는 후보 → fail-closed VetoInput(모든 게이트 막힘)."""
    return VetoInput(
        symbol=symbol, mode=mode, universe=universe,
        per_trade_risk_pct=_NO_WEIGHT, position_weight=_NO_WEIGHT,
        stop_loss_pct=0.0, regime=None,
    )


async def run_phase1_dry_run(
    *,
    scanner,
    decision_provider,
    policy: Policy,
    account_phase: str,
    risk_mode_name: str,
    regime_name: str,
    compass_state: str,
    contexts: dict[str, CandidateContext],
    report_date: str,
    executor: SimulatedExecutor | None = None,
) -> Phase1Result:
    """Phase 1 흐름을 배선해 dry-run 리포트 + 시뮬 주문을 만든다(실주문 0)."""
    mode = policy.mode(risk_mode_name)
    if mode is None:
        raise ValueError(f"알 수 없는 risk_mode: {risk_mode_name!r}")

    executor = executor or SimulatedExecutor()
    universe = policy.universe

    candidates = await scanner.scan()

    rows = []
    suggestions: dict[str, WeightSuggestion] = {}
    for cand in candidates:
        symbol = cand.symbol
        raw = (await decision_provider.decide(DecisionInput(cand, dict(cand.detail)))).decision
        entry = universe.get(symbol)
        tier = entry.primary_tier if entry is not None else None

        ctx = contexts.get(symbol)
        if ctx is None:
            veto_input = _no_context_veto_input(symbol, mode, universe)
            rationale = "시장 컨텍스트 없음 → fail-closed veto"
        else:
            sug = suggest_position_weight(account_phase, tier or "", mode, ctx.stop_loss_pct, policy.concentration)
            suggestions[symbol] = sug
            # 구체 비중 없으면(small_only/rejected/None) fail-closed → veto → 주문 없음.
            weight = sug.suggested_weight if sug.suggested_weight is not None else _NO_WEIGHT
            veto_input = _veto_input_for(symbol, mode, universe, weight, ctx)
            rationale = f"weight 제안: {sug.status}"

        # RiskGate 게이트 + 시뮬 주문 생성(통과 시에만). 우회 불가.
        executor.submit(veto_input, raw, ctx.quantity if ctx is not None else 0)
        rows.append(build_dry_run_decision(veto_input, raw, rationale=rationale))

    report = build_dry_run_report(
        report_date=report_date,
        account_phase=account_phase,
        risk_mode=risk_mode_name,
        regime=regime_name,
        compass_state=compass_state,
        decisions=tuple(rows),
    )
    return Phase1Result(report, executor.simulated_orders, suggestions)
