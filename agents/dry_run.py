"""검토용 dry-run 판단 리포트 — 주문 0건.

후보별 알고리즘/LLM 제안(Decision) + 헌법 hard-veto(algorithms.policy)를 종합해 사람이 검토하는
판단 리포트를 조립한다. 형식은 docs/templates/decision_report_template.md를 따른다. 에이전트 출력을
조립하는 I/O-adjacent 작업이라 agents/에 둔다.

CRITICAL (불변): orders_placed는 항상 0. 이 모듈은 어떤 경로로도 주문/브로커/executor/live 코드를
부르지 않는다. BUY 판단이 나와도 주문은 발생하지 않는다(검토용 리포트). DryRunReport.orders_placed는
필드가 아니라 항상 0을 돌려주는 property로 구조적으로 강제한다.

CRITICAL (RiskGate 최종권 — ADR-003/005): hard-veto가 막으면 effective_decision은 BUY가 될 수 없다
(HOLD 강등). 알고리즘/LLM의 BUY가 veto를 덮어쓰지 못한다. veto는 진입 게이트이므로 SELL(청산)/HOLD는
강등하지 않는다.

spec: specs/dry_run.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.decision import Decision
from algorithms.policy import (
    VetoInput,
    VetoResult,
    evaluate_hard_veto,
    tier_status,
)

if TYPE_CHECKING:
    from agents.sim_portfolio import PortfolioSnapshot


@dataclass(frozen=True)
class DryRunDecision:
    """후보 1건의 dry-run 판단 행(템플릿 per-candidate)."""

    symbol: str
    tier: str | None
    status: str | None
    veto: VetoResult
    raw_decision: Decision        # 알고리즘/LLM 제안
    effective_decision: Decision  # veto 반영 최종(BUY는 veto면 HOLD)
    position_weight: float
    account_loss_pct: float
    rationale: str


@dataclass(frozen=True)
class DryRunReport:
    """dry-run 리포트(헤더 + 후보별 판단 + 불변식). orders_placed는 항상 0(property)."""

    report_date: str
    account_phase: str
    risk_mode: str
    regime: str
    compass_state: str
    decisions: tuple[DryRunDecision, ...]
    mdd_hard_stop_pct: float = 0.20
    no_return_guarantee: bool = True
    portfolio_snapshot: "PortfolioSnapshot | None" = None

    @property
    def orders_placed(self) -> int:
        """항상 0 — dry-run은 주문을 내지 않는다(구조적 불변식)."""
        return 0

    @property
    def riskgate_vetoes(self) -> int:
        """veto된 후보 수."""
        return sum(1 for d in self.decisions if not d.veto.passed)

    @property
    def review_buys(self) -> tuple[str, ...]:
        """effective BUY 심볼(사람 검토용 — 주문 아님)."""
        return tuple(d.symbol for d in self.decisions if d.effective_decision is Decision.BUY)


def build_dry_run_decision(
    veto_input: VetoInput,
    raw_decision: Decision,
    *,
    rationale: str = "",
) -> DryRunDecision:
    """후보 1건의 hard-veto + 판단을 조립한다. veto면 BUY는 HOLD로 강등(RiskGate 최종권)."""
    veto = evaluate_hard_veto(veto_input)

    if raw_decision is Decision.BUY and not veto.passed:
        effective = Decision.HOLD  # 진입 차단 — BUY가 veto를 덮어쓰지 못한다.
    else:
        effective = raw_decision

    entry = veto_input.universe.get(veto_input.symbol)
    tier = entry.primary_tier if entry is not None else None
    status = tier_status(veto_input.symbol, veto_input.universe)

    parts = [p for p in (rationale,) if p]
    if not veto.passed:
        parts.append("VETO: " + "; ".join(veto.reasons))
    full_rationale = " | ".join(parts) if parts else "통과(검토용 — 주문 없음)."

    return DryRunDecision(
        symbol=veto_input.symbol,
        tier=tier,
        status=status,
        veto=veto,
        raw_decision=raw_decision,
        effective_decision=effective,
        position_weight=veto_input.position_weight,
        account_loss_pct=veto.risk_check.account_loss_pct,
        rationale=full_rationale,
    )


def build_dry_run_report(
    *,
    report_date: str,
    account_phase: str,
    risk_mode: str,
    regime: str,
    compass_state: str,
    decisions: tuple[DryRunDecision, ...],
    portfolio_snapshot: "PortfolioSnapshot | None" = None,
) -> DryRunReport:
    """헤더 + 후보별 판단을 리포트로 조립한다. orders_placed는 property로 0 고정."""
    return DryRunReport(
        report_date=report_date,
        account_phase=account_phase,
        risk_mode=risk_mode,
        regime=regime,
        compass_state=compass_state,
        decisions=tuple(decisions),
        portfolio_snapshot=portfolio_snapshot,
    )


def format_dry_run_report(report: DryRunReport) -> str:
    """사람이 읽는 dry-run 리포트 텍스트(템플릿 형식). 자동 라이브 진입 없음."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Dry-Run Decision Report (DRY-RUN — 주문 미발생, 사람 검토용)")
    lines.append("=" * 72)
    lines.append(
        f"report_date={report.report_date}  account_phase={report.account_phase}  "
        f"risk_mode={report.risk_mode}  regime={report.regime}  compass={report.compass_state}"
    )
    lines.append("")
    lines.append(f"  {'symbol':<8}{'tier':<6}{'status':<14}{'raw':<6}{'effective':<10}{'acct_loss':>10}  veto")
    for d in report.decisions:
        lines.append(
            f"  {d.symbol:<8}{str(d.tier):<6}{str(d.status):<14}"
            f"{d.raw_decision.value:<6}{d.effective_decision.value:<10}"
            f"{d.account_loss_pct:>10.4f}  {'PASS' if d.veto.passed else 'VETO'}"
        )
        if not d.veto.passed:
            for r in d.veto.reasons:
                lines.append(f"      - {r}")
    lines.append("")
    lines.append("[푸터 / 불변식]")
    lines.append(f"  orders_placed       : {report.orders_placed}            # 항상 0 (DRY-RUN)")
    lines.append(f"  riskgate_vetoes     : {report.riskgate_vetoes}")
    lines.append(f"  review_buys         : {', '.join(report.review_buys) or '(none)'}")
    lines.append(f"  mdd_hard_stop       : {report.mdd_hard_stop_pct:.2f} (불변)")
    lines.append(f"  no_return_guarantee : {str(report.no_return_guarantee).lower()}")
    lines.append("  note                : 자동 라이브 진입 없음. 사람 검토용 리포트.")
    snap = report.portfolio_snapshot
    if snap is not None:
        lines.append("")
        lines.append("[시뮬 포트폴리오 스냅샷]")
        lines.append(
            f"  starting_cash={snap.starting_cash:.2f}  cash={snap.cash:.2f}  "
            f"market_value={snap.market_value:.2f}  equity={snap.equity:.2f}"
        )
        lines.append(
            f"  realized_pnl={snap.realized_pnl:.2f}  unrealized_pnl={snap.unrealized_pnl:.2f}  "
            f"data_missing={str(snap.data_missing).lower()}"
        )
        lines.append(
            f"  open_positions={snap.open_positions}  trades={snap.trade_count}  "
            f"real_orders_placed={snap.real_orders_placed}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)
