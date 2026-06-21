"""트레일링 스톱/청산 정책 딥다이브 — 청산 프로파일별 성과·귀속을 본다(순수 측정).

요약·청산사유 귀속·홀딩일·트레일링 영향·마크다운은 순수 함수. 변형 재시뮬은 러너가 run_sim 청산
플래그(stop/trail/max_hold)만 바꿔 만든다. 진입/유니버스/스캐너/디시전/사이징/RiskGate 미변경.

미청산 포지션은 백테스트 끝에서 마지막 종가로 마크(exit_reason 'open', 미실현). avg holding days는
청산 leg만. all_exits_off처럼 청산이 거의 없는 변형은 diagnostic_only로 표기하고 best 후보에서 제외.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/exit_deep_dive.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from agents.signal_ablation import quarterly_pnl
from agents.universe_bias import compute_top_shares

# 청산 사유 원시값 → 리포트 버킷.
_EXIT_BUCKETS = {
    "time_stop": "time_stop",
    "trailing_stop_hit": "trailing_stop",
    "stop_loss_hit": "stop_loss",
    "OPEN": "open",
}
_BUCKET_ORDER = ("time_stop", "trailing_stop", "stop_loss", "other", "open")


@dataclass(frozen=True)
class ExitReasonStat:
    reason: str
    count: int
    total_pnl: float
    avg_pnl: float | None


@dataclass(frozen=True)
class ExitVariantResult:
    name: str
    stop: float | None
    trail: float | None
    max_hold: int | None
    cumulative_return: float | None
    total_pnl: float | None
    max_drawdown: float | None
    return_over_mdd: float | None
    win_rate: float | None
    trades: int
    avg_trade_pnl: float | None
    median_trade_pnl: float | None
    avg_holding_days: float | None
    top1_symbol: str | None
    top1_share: float | None
    top3_symbols: tuple[str, ...]
    top3_share: float | None
    best_symbol: str | None
    worst_symbol: str | None
    quarterly: tuple[tuple[str, float], ...]
    exit_reasons: tuple[ExitReasonStat, ...]
    spy_return: float | None
    qqq_return: float | None
    beats_spy: bool | None
    beats_qqq: bool | None
    diagnostic_only: bool = False
    note: str | None = None


@dataclass(frozen=True)
class SymbolImpact:
    symbol: str
    baseline_pnl: float
    no_trail_pnl: float
    delta: float        # no_trail − baseline (양수면 트레일링이 그 심볼에 손해)


@dataclass(frozen=True)
class ExitDeepDive:
    variants: tuple[ExitVariantResult, ...]
    trailing_hurt: tuple[SymbolImpact, ...]
    trailing_helped: tuple[SymbolImpact, ...]
    best_by_ratio: str | None
    best_by_pnl: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _priced(legs):
    return [l for l in legs if l.pnl is not None]


def compute_holding_days(legs):
    """청산 leg의 평균 보유일(entry→exit). 미청산/날짜 없음 제외. 없으면 None."""
    days = []
    for l in legs:
        if l.exit_reason in (None, "OPEN") or not l.entry_date or not l.exit_date:
            continue
        try:
            d = (date.fromisoformat(str(l.exit_date)[:10]) - date.fromisoformat(str(l.entry_date)[:10])).days
        except ValueError:
            continue
        days.append(d)
    return (sum(days) / len(days)) if days else None


def exit_reason_breakdown(legs):
    """청산 사유별 count/total_pnl/avg_pnl(open 포함). 무가 leg는 PnL 0 취급은 안 하고 제외."""
    agg: dict[str, list] = {}
    for l in legs:
        bucket = _EXIT_BUCKETS.get(l.exit_reason, "other")
        rec = agg.setdefault(bucket, [0, 0.0, 0])   # count, pnl_sum, priced_count
        rec[0] += 1
        if l.pnl is not None:
            rec[1] += l.pnl
            rec[2] += 1
    out = []
    for bucket in _BUCKET_ORDER:
        if bucket in agg:
            c, pnl, pc = agg[bucket]
            out.append(ExitReasonStat(reason=bucket, count=c, total_pnl=pnl,
                                      avg_pnl=(pnl / pc if pc else None)))
    return tuple(out)


def per_symbol_pnl(legs):
    totals: dict[str, float] = {}
    for l in _priced(legs):
        totals[l.symbol] = totals.get(l.symbol, 0.0) + l.pnl
    return totals


def trailing_impact(base_legs, no_trail_legs, *, top=5):
    """baseline vs trail_off 심볼별 PnL delta로 트레일링에 손해/도움 본 심볼."""
    base = per_symbol_pnl(base_legs)
    nott = per_symbol_pnl(no_trail_legs)
    impacts = []
    for sym in set(base) | set(nott):
        b = base.get(sym, 0.0)
        n = nott.get(sym, 0.0)
        impacts.append(SymbolImpact(symbol=sym, baseline_pnl=b, no_trail_pnl=n, delta=n - b))
    hurt = tuple(sorted((i for i in impacts if i.delta > 0), key=lambda i: i.delta, reverse=True)[:top])
    helped = tuple(sorted((i for i in impacts if i.delta < 0), key=lambda i: i.delta)[:top])
    return hurt, helped


def summarize_variant(name, params, legs, performance, *, spy=None, qqq=None,
                      diagnostic_only=False, note=None) -> ExitVariantResult:
    """청산 변형 결과를 요약한다(true-rerun performance + leg 파생 지표). 순수."""
    stop, trail, max_hold = params
    priced = _priced(legs)
    pnls = [l.pnl for l in priced]
    ret = None if performance is None else float(performance.cumulative_return)
    mdd = None if performance is None else float(performance.max_drawdown)
    top1, s1, top3, s3, best, worst = compute_top_shares(
        [SimpleNamespace(symbol=s, total_pnl=p) for s, p in per_symbol_pnl(priced).items()])
    return ExitVariantResult(
        name=name, stop=stop, trail=trail, max_hold=max_hold,
        cumulative_return=ret, total_pnl=(float(sum(pnls)) if pnls else 0.0), max_drawdown=mdd,
        return_over_mdd=(ret / mdd if (ret is not None and mdd) else None),
        win_rate=(None if performance is None else getattr(performance, "win_rate", None)),
        trades=(0 if performance is None else int(performance.num_trades)),
        avg_trade_pnl=(statistics.fmean(pnls) if pnls else None),
        median_trade_pnl=(statistics.median(pnls) if pnls else None),
        avg_holding_days=compute_holding_days(legs),
        top1_symbol=top1, top1_share=s1, top3_symbols=top3, top3_share=s3,
        best_symbol=best, worst_symbol=worst, quarterly=quarterly_pnl(priced),
        exit_reasons=exit_reason_breakdown(legs), spy_return=spy, qqq_return=qqq,
        beats_spy=(None if (ret is None or spy is None) else ret > spy),
        beats_qqq=(None if (ret is None or qqq is None) else ret > qqq),
        diagnostic_only=diagnostic_only, note=note,
    )


def _get(variants, name):
    return next((v for v in variants if v.name == name), None)


def build_exit_deep_dive(variants, trailing_hurt, trailing_helped) -> ExitDeepDive:
    """변형을 묶고 diagnostic 제외 best 후보 + 경고를 만든다."""
    variants = tuple(variants)
    candidates = [v for v in variants if not v.diagnostic_only]
    by_ratio = [v for v in candidates if v.return_over_mdd is not None]
    by_pnl = [v for v in candidates if v.total_pnl is not None]
    best_by_ratio = max(by_ratio, key=lambda v: v.return_over_mdd).name if by_ratio else None
    best_by_pnl = max(by_pnl, key=lambda v: v.total_pnl).name if by_pnl else None

    warnings: list[str] = []
    base = _get(variants, "baseline")
    if base and best_by_ratio and best_by_ratio != "baseline":
        warnings.append(
            f"best return/MDD 후보는 {best_by_ratio} (baseline 아님) — 단일 짧은 강세 구간 측정, "
            "잠금 변경 전 추가 검증 필요(과대 주장 금지)"
        )
    if any(v.diagnostic_only for v in variants):
        warnings.append("all_exits_off는 diagnostic only — 청산 거의 없어 소수 포지션 왜곡, best 후보 제외")
    return ExitDeepDive(
        variants=variants, trailing_hurt=tuple(trailing_hurt), trailing_helped=tuple(trailing_helped),
        best_by_ratio=best_by_ratio, best_by_pnl=best_by_pnl, warnings=tuple(warnings),
    )


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _num(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _row(v: ExitVariantResult) -> str:
    tag = " *(diag)*" if v.diagnostic_only else ""
    return (
        f"| {v.name}{tag} | {_pct(v.stop, '{:.0%}')}/{_pct(v.trail, '{:.0%}')}/{v.max_hold} | "
        f"{_pct(v.cumulative_return)} | {_num(v.total_pnl)} | {_pct(v.max_drawdown)} | "
        f"{_num(v.return_over_mdd)} | {_pct(v.win_rate, '{:.0%}')} | {v.trades} | "
        f"{_num(v.avg_trade_pnl)} | {_num(v.avg_holding_days, '{:.0f}')} | "
        f"{v.top1_symbol or '-'} {_pct(v.top1_share, '{:.0%}')} | {_pct(v.top3_share, '{:.0%}')} | "
        f"{v.beats_spy} | {v.beats_qqq} |"
    )


def format_exit_deep_dive_markdown(report: ExitDeepDive) -> str:
    """마크다운 리포트(reports/exit_policy_deep_dive.md). 측정 보조 — 매매 미사용."""
    v = report.variants
    base = _get(v, "baseline")
    trail_off = _get(v, "trail_off")
    lines: list[str] = []
    lines.append("# Exit Policy / Trailing Stop Deep Dive (측정 - 실주문 없음, 진입 next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. "
                 "진입 모델·유니버스·스캐너/디시전/사이징/RiskGate·베이스라인 미변경. 청산 플래그만 변형.")
    lines.append("")
    lines.append("**방법론**: 모든 변형은 run_sim 청산 플래그(stop/trail/max_hold)만 바꾼 true-rerun. "
                 "미청산 포지션은 백테스트 끝에서 마지막 종가로 마크(exit_reason `open`, 미실현 PnL). "
                 "avg holding days는 청산된 leg만 집계. `all_exits_off`는 청산이 거의 없어 소수 포지션이 "
                 "끝까지 보유돼 왜곡되므로 **diagnostic only**(best 후보 제외).")
    lines.append("")
    lines.append("| variant | stop/trail/hold | return | PnL | MDD | ret/MDD | win | trades "
                 "| avg PnL | avg hold(d) | top1 | top3 | >SPY | >QQQ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in v:
        lines.append(_row(r))
    lines.append("")

    lines.append(f"**best by return/MDD (diagnostic 제외)**: {report.best_by_ratio}  ·  "
                 f"**best by raw PnL**: {report.best_by_pnl}")
    lines.append("")

    # 청산 사유 귀속 (baseline).
    if base:
        lines.append("## 청산 사유 귀속 (baseline)")
        lines.append("")
        lines.append("| reason | count | total PnL | avg PnL |")
        lines.append("|---|---|---|---|")
        for e in base.exit_reasons:
            lines.append(f"| {e.reason} | {e.count} | {_num(e.total_pnl)} | {_num(e.avg_pnl)} |")
        lines.append("")

    # 트레일링 영향 심볼.
    lines.append("## 트레일링 스톱 영향 (baseline vs trail_off, 심볼별 ΔPnL = trail_off − baseline)")
    lines.append("")
    lines.append("- **트레일링에 가장 손해 본 심볼 (Δ>0, 트레일링이 깎음)**: " +
                 (", ".join(f"{i.symbol} {i.delta:+.0f}" for i in report.trailing_hurt) or "없음"))
    lines.append("- **트레일링이 가장 도움 준 심볼 (Δ<0, 트레일링이 보호)**: " +
                 (", ".join(f"{i.symbol} {i.delta:+.0f}" for i in report.trailing_helped) or "없음"))
    lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    if base and trail_off:
        # 1. 일관 vs 소수 심볼.
        n_hurt = len(report.trailing_hurt)
        n_helped = len(report.trailing_helped)
        lines.append(f"- **트레일링이 일관되게 해치나, 소수 심볼?** trail_off PnL {_num(trail_off.total_pnl)} "
                     f"vs baseline {_num(base.total_pnl)}. 손해 본 심볼 {n_hurt} / 도움 본 심볼 {n_helped} "
                     "— 소수 심볼 쏠림 여부는 위 목록 참고.")
        # 2. return/MDD vs raw PnL.
        better_ratio = (trail_off.return_over_mdd or 0) > (base.return_over_mdd or 0)
        better_pnl = (trail_off.total_pnl or 0) > (base.total_pnl or 0)
        lines.append(f"- **트레일링 비활성이 return/MDD 개선? raw PnL만?** trail_off ret/MDD "
                     f"{_num(trail_off.return_over_mdd)} vs {_num(base.return_over_mdd)} "
                     f"({'개선' if better_ratio else '비개선'}); PnL {'개선' if better_pnl else '비개선'}.")
    t10, t30 = _get(v, "trail_10"), _get(v, "trail_30")
    if base and t10:
        lines.append(f"- **낮은 트레일링(10%)은 drawdown 보호하나 승자 일찍 자르나?** trail_10 MDD "
                     f"{_pct(t10.max_drawdown)} vs baseline {_pct(base.max_drawdown)}, "
                     f"PnL {_num(t10.total_pnl)} (win {_pct(t10.win_rate, '{:.0%}')}, "
                     f"avg hold {_num(t10.avg_holding_days, '{:.0f}')}d).")
    if t30 and trail_off:
        lines.append(f"- **높은 트레일링(30%)은 비활성과 비슷?** trail_30 PnL {_num(t30.total_pnl)} / "
                     f"trades {t30.trades} vs trail_off PnL {_num(trail_off.total_pnl)} / trades {trail_off.trades}.")
    holds = [(h, _get(v, f"hold_{h}_trailoff")) for h in (45, 60, 75, 90)]
    holds = [(h, r) for h, r in holds if r is not None and r.return_over_mdd is not None]
    if holds:
        best_hold = max(holds, key=lambda x: x[1].return_over_mdd)
        lines.append(f"- **트레일링 비활성 시 60일 max holding이 최선?** ret/MDD 기준 best hold = "
                     f"{best_hold[0]}일 ({_num(best_hold[1].return_over_mdd)}); "
                     + ", ".join(f"{h}d {_num(r.return_over_mdd)}" for h, r in holds) + ".")
    lines.append(f"- **best 후보?** return/MDD: {report.best_by_ratio}, raw PnL: {report.best_by_pnl} "
                 "(diagnostic 제외).")
    lines.append("- **잠긴 베이스라인 유지?** 권장: **유지**. 더 나은 후보가 보여도 단일 짧은 강세 구간 "
                 "측정이라 워크포워드/장기 검증 전에는 잠금 변경 금지(과대 주장 금지).")
    lines.append("")

    if base:
        lines.append("## 분기 PnL (baseline)")
        lines.append("")
        for q, pnl in base.quarterly:
            lines.append(f"- {q}: {pnl:.2f}")
        lines.append(f"- 벤치마크: SPY {_pct(base.spy_return)} / QQQ {_pct(base.qqq_return)}")
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
