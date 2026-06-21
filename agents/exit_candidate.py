"""후보 청산 정책 검증 — hold_45_trailoff가 베이스라인 후보로 강건한지 본다(순수 측정).

연도/positive-quarter 집계, 심볼 제거 delta, 비교/판정/마크다운은 순수 함수. 정책별 재시뮬·LOO·
슬리피지는 러너가 만들어 넣는다. ExitVariantResult/슬리피지/LOO 빌딩블록을 재사용. 진입/유니버스/
스캐너/디시전/사이징/RiskGate 미변경. **이 단계에서 베이스라인 승격 없음.**

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/exit_candidate.md
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DropResult:
    name: str
    total_pnl: float | None
    cumulative_return: float | None
    delta_pnl: float | None       # drop − full (음수면 그 심볼이 기여)


@dataclass(frozen=True)
class PolicyValidation:
    name: str
    stop: float | None
    trail: float | None
    max_hold: int | None
    full: object                  # ExitVariantResult (return/MDD/win/top/quarterly/exit_reasons...)
    eq_return: float | None
    yearly: tuple[tuple[str, float], ...]
    positive_quarters: int
    active_quarters: int
    slippage: tuple[object, ...]  # SlippageStress(slippage/total_pnl/return_pct)
    loo: tuple[object, ...]       # robustness LeaveOneOut(excluded_symbol/total_pnl/total_pnl_diff/...)
    worst_drop: object | None
    no_mu: DropResult | None
    no_arm: DropResult | None
    no_top3: DropResult | None


@dataclass(frozen=True)
class CandidateValidationReport:
    policies: tuple[PolicyValidation, ...]
    baseline_name: str
    candidate_name: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _year(date_str):
    if not date_str or len(str(date_str)) < 4:
        return None
    try:
        return str(int(str(date_str)[:4]))
    except ValueError:
        return None


def yearly_pnl(legs):
    """청산 연도별 PnL 합(미청산/무가 제외)."""
    agg: dict[str, float] = {}
    for l in legs:
        if l.pnl is None:
            continue
        y = _year(l.exit_date)
        if y is None:
            continue
        agg[y] = agg.get(y, 0.0) + l.pnl
    return tuple((y, agg[y]) for y in sorted(agg))


def positive_active_quarters(quarterly):
    """(양수 분기 수, 활성 분기 수). 활성 = PnL이 0이 아닌 분기."""
    active = [q for q in quarterly if q[1] != 0]
    positive = [q for q in active if q[1] > 0]
    return len(positive), len(active)


def make_drop(name, full_pnl, drop_pnl, drop_return) -> DropResult:
    delta = None if (drop_pnl is None or full_pnl is None) else drop_pnl - full_pnl
    return DropResult(name=name, total_pnl=drop_pnl, cumulative_return=drop_return, delta_pnl=delta)


def _get(policies, name):
    return next((p for p in policies if p.name == name), None)


def _slip_at(policy, slip):
    return next((s for s in policy.slippage if abs(s.slippage - slip) < 1e-9), None)


def build_candidate_validation(policies, *, baseline_name, candidate_name) -> CandidateValidationReport:
    """정책들을 묶고 후보 vs baseline 판정 경고를 만든다(승격 아님)."""
    policies = tuple(policies)
    base = _get(policies, baseline_name)
    cand = _get(policies, candidate_name)
    warnings: list[str] = []

    if base and cand:
        for slip in (0.005, 0.01):
            bs, cs = _slip_at(base, slip), _slip_at(cand, slip)
            if bs and cs and cs.total_pnl <= bs.total_pnl:
                warnings.append(
                    f"{slip:.1%} 슬리피지에서 후보 PnL {cs.total_pnl:.0f} ≤ baseline {bs.total_pnl:.0f} "
                    "— 슬리피지 후 우위 사라짐"
                )
        bf, cf = base.full, cand.full
        if bf.return_over_mdd is not None and cf.return_over_mdd is not None:
            if cf.return_over_mdd <= bf.return_over_mdd:
                warnings.append(
                    f"후보 return/MDD {cf.return_over_mdd:.2f} ≤ baseline {bf.return_over_mdd:.2f} "
                    "— ret/MDD 개선 아님(raw PnL만일 수 있음)"
                )
        if cand.no_arm and cand.no_arm.delta_pnl is not None and cf.total_pnl:
            if -cand.no_arm.delta_pnl / cf.total_pnl >= 0.25:
                warnings.append(
                    f"후보: ARM 제거 시 PnL {-cand.no_arm.delta_pnl / cf.total_pnl:.0%} 감소 — ARM 쏠림"
                )

    warnings.append("측정 단계 — 단일 짧은 강세 구간. 베이스라인 승격 없음(no promotion yet).")
    return CandidateValidationReport(
        policies=policies, baseline_name=baseline_name, candidate_name=candidate_name,
        warnings=tuple(warnings),
    )


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _num(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _policy_row(p: PolicyValidation) -> str:
    f = p.full
    return (
        f"| {p.name} | {_pct(p.stop, '{:.0%}')}/{_pct(p.trail, '{:.0%}')}/{p.max_hold} | "
        f"{_pct(f.cumulative_return)} | {_num(f.total_pnl)} | {_pct(f.max_drawdown)} | "
        f"{_num(f.return_over_mdd)} | {_pct(f.win_rate, '{:.0%}')} | {f.trades} | "
        f"{_num(f.avg_trade_pnl)} | {_num(f.avg_holding_days, '{:.0f}')} | "
        f"{f.top1_symbol or '-'} {_pct(f.top1_share, '{:.0%}')} | {_pct(f.top3_share, '{:.0%}')} | "
        f"{p.positive_quarters}/{p.active_quarters} | {f.beats_spy} | {f.beats_qqq} |"
    )


def _drop_line(label, d: DropResult | None):
    if d is None:
        return f"  - {label}: n/a"
    return (f"  - {label}: PnL {_num(d.total_pnl)} (return {_pct(d.cumulative_return)}, "
            f"ΔPnL {_num(d.delta_pnl, '{:+.2f}')})")


def format_candidate_validation_markdown(report: CandidateValidationReport) -> str:
    """마크다운 리포트(reports/exit_candidate_validation.md). 측정 보조 — 매매 미사용."""
    base = _get(report.policies, report.baseline_name)
    cand = _get(report.policies, report.candidate_name)
    lines: list[str] = []
    lines.append("# Candidate Exit Policy Validation (측정 - 실주문 없음, 진입 next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 진입 모델·유니버스·"
                 "스캐너/디시전/사이징/RiskGate·베이스라인 미변경. **이 단계에서 베이스라인 승격 없음.**")
    lines.append("")
    lines.append("| policy | stop/trail/hold | return | PnL | MDD | ret/MDD | win | trades "
                 "| avg PnL | hold(d) | top1 | top3 | +Q/활성Q | >SPY | >QQQ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p in report.policies:
        lines.append(_policy_row(p))
    lines.append("")

    # 정책별 강건성.
    for p in report.policies:
        lines.append(f"## {p.name} — 강건성")
        lines.append("")
        lines.append(f"- 벤치마크: SPY {_pct(p.full.spy_return)} / QQQ {_pct(p.full.qqq_return)} / "
                     f"equal-weight {_pct(p.eq_return)}")
        lines.append("- 슬리피지: " + ", ".join(
            f"{s.slippage:.2%} PnL {_num(s.total_pnl)} ({_pct(s.return_pct)})" for s in p.slippage))
        lines.append(_drop_line("no_MU", p.no_mu))
        lines.append(_drop_line("no_ARM", p.no_arm))
        lines.append(_drop_line("no_top3", p.no_top3))
        if p.worst_drop is not None:
            lines.append(f"  - LOO worst-drop: {p.worst_drop.excluded_symbol} → "
                         f"PnL {_num(p.worst_drop.total_pnl)} (Δ {_num(p.worst_drop.total_pnl_diff, '{:+.2f}')})")
        if p.yearly:
            lines.append("- 연도 PnL: " + ", ".join(f"{y} {pnl:.0f}" for y, pnl in p.yearly))
        if p.full.exit_reasons:
            lines.append("- 청산 사유: " + ", ".join(
                f"{e.reason} {e.count}건 {_num(e.total_pnl)}" for e in p.full.exit_reasons))
        lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    if base and cand:
        bf, cf = base.full, cand.full
        for slip in (0.005, 0.01):
            bs, cs = _slip_at(base, slip), _slip_at(cand, slip)
            if bs and cs:
                verdict = "이김" if cs.total_pnl > bs.total_pnl else "못 이김"
                lines.append(f"- **슬리피지 {slip:.1%} 후 후보가 baseline을 이기나?** 후보 {_num(cs.total_pnl)} "
                             f"vs baseline {_num(bs.total_pnl)} → {verdict}.")
        ratio_better = (cf.return_over_mdd or 0) > (bf.return_over_mdd or 0)
        pnl_better = (cf.total_pnl or 0) > (bf.total_pnl or 0)
        lines.append(f"- **return/MDD 개선? raw PnL만?** 후보 ret/MDD {_num(cf.return_over_mdd)} vs "
                     f"baseline {_num(bf.return_over_mdd)} ({'개선' if ratio_better else '비개선'}); "
                     f"PnL {'개선' if pnl_better else '비개선'}.")
        if cand.no_arm and cand.no_arm.delta_pnl is not None and cf.total_pnl:
            share = -cand.no_arm.delta_pnl / cf.total_pnl
            lines.append(f"- **개선이 대부분 ARM인가?** 후보 ARM 제거 ΔPnL {_num(cand.no_arm.delta_pnl, '{:+.2f}')} "
                         f"({share:+.0%}) — {'ARM 쏠림 큼' if share >= 0.25 else 'ARM 쏠림 제한적'}.")
        # 동일 ablation 끼리(candidate-drop vs baseline-drop) 공정 비교.
        survive = []
        for label, cd, bd in (("no_MU", cand.no_mu, base.no_mu), ("no_ARM", cand.no_arm, base.no_arm),
                              ("no_top3", cand.no_top3, base.no_top3)):
            if cd and bd and cd.total_pnl is not None and bd.total_pnl is not None:
                survive.append(f"{label} cand {_num(cd.total_pnl)} {'>' if cd.total_pnl > bd.total_pnl else '≤'} "
                               f"base {_num(bd.total_pnl)}")
        lines.append(f"- **no_MU/no_ARM/no_top3 생존?(동일 제거 비교)** " + (", ".join(survive) or "n/a") +
                     " — 같은 심볼 제거 시 후보가 baseline을 계속 이기는지.")
        lines.append(f"- **분기 전반 강한가?** 후보 +Q/활성Q {cand.positive_quarters}/{cand.active_quarters}, "
                     f"baseline {base.positive_quarters}/{base.active_quarters}.")
        safer = (cf.max_drawdown or 1) < (bf.max_drawdown or 0)
        lines.append(f"- **더 안전/위험/공격적?** 후보 MDD {_pct(cf.max_drawdown)} vs baseline {_pct(bf.max_drawdown)}, "
                     f"hold {cand.max_hold} vs {base.max_hold}, trades {cf.trades} vs {bf.trades} → "
                     f"{'더 안전' if safer else '더 공격적(MDD 동등·상승, 회전↑)'}.")
    lines.append("- **지금 baseline을 잠근 채 둬야 하나?** **예.** 단일 짧은 2025-2026 강세 구간 측정 — 승격 없음.")
    lines.append("- **승격 전 필요한 증거?** (1) 강세장 밖/장기 워크포워드에서 ret/MDD 우위 지속, "
                 "(2) ARM·top3 제거 후에도 baseline 우위 유지, (3) 슬리피지 1%에서도 우위, "
                 "(4) 분기 음수 없음 — 4개 모두 충족 시에만 후보 승격 검토.")
    lines.append("")

    if report.warnings:
        lines.append("## 경고")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    lines.append(f"`real_orders_placed = {report.real_orders_placed}`")
    lines.append("")
    return "\n".join(lines)
