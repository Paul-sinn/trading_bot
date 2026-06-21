"""시그널 제거(ablation) 분석 — 무엇이 결과를 끌고 가는지 본다(순수 측정).

요약·shadow 제거·마크다운 포맷은 순수 함수. 청산/심볼 true-rerun은 러너가 run_sim으로,
shadow 근사는 실현 트레이드를 진입 피처로 제거해 만든다. 상태/매매/veto/전략/기본 유니버스 미변경.
갭 가드·winner extension 미적용, next-open 미사용.

shadow 변형은 "실현 트레이드 제거"일 뿐 진짜 전략 재시뮬이 아니다 — equity 경로를 재현하지 않으므로
MDD/return·MDD는 n/a로 둔다(과대 주장 금지).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/signal_ablation.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from types import SimpleNamespace

from agents.universe_bias import compute_top_shares   # top1/top3 share 재사용

MODE_TRUE = "true-rerun"
MODE_SHADOW = "shadow-approx"


@dataclass(frozen=True)
class AblationResult:
    name: str
    mode: str
    cumulative_return: float | None
    total_pnl: float | None
    max_drawdown: float | None
    return_over_mdd: float | None
    win_rate: float | None
    trades: int
    avg_trade_pnl: float | None
    median_trade_pnl: float | None
    top1_symbol: str | None
    top1_share: float | None
    top3_symbols: tuple[str, ...]
    top3_share: float | None
    best_symbol: str | None
    worst_symbol: str | None
    quarterly: tuple[tuple[str, float], ...]
    spy_return: float | None
    qqq_return: float | None
    eq_return: float | None
    beats_spy: bool | None
    beats_qqq: bool | None
    note: str | None = None


@dataclass(frozen=True)
class AblationReport:
    variants: tuple[AblationResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _quarter(date_str):
    if not date_str or len(str(date_str)) < 7:
        return None
    try:
        y, m = int(str(date_str)[:4]), int(str(date_str)[5:7])
    except ValueError:
        return None
    return f"{y}-Q{(m - 1) // 3 + 1}"


def quarterly_pnl(legs):
    """청산 분기별 PnL 합(미청산/무가 leg 제외)."""
    agg: dict[str, float] = {}
    for l in legs:
        if l.pnl is None:
            continue
        q = _quarter(l.exit_date)
        if q is None:
            continue
        agg[q] = agg.get(q, 0.0) + l.pnl
    return tuple((q, agg[q]) for q in sorted(agg))


def _symbol_perf(legs):
    totals: dict[str, float] = {}
    for l in legs:
        if l.pnl is None:
            continue
        totals[l.symbol] = totals.get(l.symbol, 0.0) + l.pnl
    return [SimpleNamespace(symbol=s, total_pnl=v) for s, v in totals.items()]


def _feature_value(leg, snapshot_index, feature):
    snap = snapshot_index.get((leg.symbol, leg.entry_date))
    return None if snap is None else getattr(snap, feature, None)


def shadow_drop(legs, snapshot_index, feature, *, is_flag=False):
    """약한 진입 피처 leg를 제거한다(shadow 근사). 판단 불가(스냅샷/값 없음) leg는 유지.

    is_flag=True면 flag가 False인 leg 제거. 아니면 피처 중앙값 미만 leg 제거.
    """
    if is_flag:
        kept = [l for l in legs if _feature_value(l, snapshot_index, feature) is not False]
        return tuple(kept)
    vals = [v for l in legs if (v := _feature_value(l, snapshot_index, feature)) is not None]
    if not vals:
        return tuple(legs)
    median = statistics.median(vals)
    kept = []
    for l in legs:
        v = _feature_value(l, snapshot_index, feature)
        if v is not None and v < median:
            continue   # 약한(중앙값 미만) leg 제거
        kept.append(l)
    return tuple(kept)


def summarize(name, mode, legs, *, starting_cash, performance=None,
              spy=None, qqq=None, eq=None, note=None) -> AblationResult:
    """leg 묶음(+선택적 performance)에서 ablation 결과를 만든다(순수).

    performance가 있으면(true-rerun) return/MDD/win/trades를 그것에서, 없으면(shadow) leg에서 계산.
    shadow는 MDD/return·MDD를 None으로 둔다.
    """
    priced = [l for l in legs if l.pnl is not None]
    pnls = [l.pnl for l in priced]
    total = float(sum(pnls)) if pnls else 0.0

    if performance is not None:
        ret = float(performance.cumulative_return)
        mdd = float(performance.max_drawdown)
        win = getattr(performance, "win_rate", None)
        trades = int(performance.num_trades)
        ret_over_mdd = (ret / mdd) if mdd else None
    else:
        ret = (total / starting_cash) if starting_cash else None
        mdd = None
        wins = sum(1 for p in pnls if p > 0)
        win = (wins / len(pnls)) if pnls else None
        trades = len(priced)
        ret_over_mdd = None

    avg = statistics.fmean(pnls) if pnls else None
    median = statistics.median(pnls) if pnls else None
    top1, top1_share, top3, top3_share, best, worst = compute_top_shares(_symbol_perf(priced))

    return AblationResult(
        name=name, mode=mode, cumulative_return=ret, total_pnl=total, max_drawdown=mdd,
        return_over_mdd=ret_over_mdd, win_rate=win, trades=trades,
        avg_trade_pnl=avg, median_trade_pnl=median,
        top1_symbol=top1, top1_share=top1_share, top3_symbols=top3, top3_share=top3_share,
        best_symbol=best, worst_symbol=worst, quarterly=quarterly_pnl(priced),
        spy_return=spy, qqq_return=qqq, eq_return=eq,
        beats_spy=(None if (ret is None or spy is None) else ret > spy),
        beats_qqq=(None if (ret is None or qqq is None) else ret > qqq),
        note=note,
    )


def _get(variants, name):
    return next((v for v in variants if v.name == name), None)


def build_ablation(variants) -> AblationReport:
    """변형들을 묶고 관찰 경고를 만든다(판단 아님)."""
    variants = tuple(variants)
    base = _get(variants, "baseline")
    warnings: list[str] = []

    if base and base.total_pnl:
        no_exit = _get(variants, "no_exit_controls")
        if no_exit and no_exit.total_pnl is not None and no_exit.total_pnl > base.total_pnl:
            warnings.append(
                f"no_exit_controls PnL {no_exit.total_pnl:.0f} > baseline {base.total_pnl:.0f} "
                "— 청산 통제가 수익을 깎고 있을 수 있음(측정 근사)"
            )
        no_mu = _get(variants, "no_MU")
        if no_mu and no_mu.total_pnl is not None and base.total_pnl > 0:
            drop = (base.total_pnl - no_mu.total_pnl) / base.total_pnl
            if drop >= 0.25:
                warnings.append(f"MU 제거 시 PnL {drop:.0%} 감소 — MU 의존")

    if any(v.mode == MODE_SHADOW for v in variants):
        warnings.append("shadow-approx 변형은 실현 트레이드 제거 근사 — 진짜 전략 재시뮬 아님(MDD n/a)")

    return AblationReport(variants=variants, warnings=tuple(warnings))


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _row(v: AblationResult) -> str:
    return (
        f"| {v.name} | {v.mode} | {_pct(v.cumulative_return)} | "
        f"{'n/a' if v.total_pnl is None else f'{v.total_pnl:.2f}'} | {_pct(v.max_drawdown)} | "
        f"{_pct(v.return_over_mdd, '{:.2f}')} | {_pct(v.win_rate, '{:.0%}')} | {v.trades} | "
        f"{'n/a' if v.avg_trade_pnl is None else f'{v.avg_trade_pnl:.2f}'} | "
        f"{'n/a' if v.median_trade_pnl is None else f'{v.median_trade_pnl:.2f}'} | "
        f"{v.top1_symbol or '-'} {_pct(v.top1_share, '{:.0%}')} | {_pct(v.top3_share, '{:.0%}')} | "
        f"{v.best_symbol or '-'}/{v.worst_symbol or '-'} | {v.beats_spy} | {v.beats_qqq} |"
    )


def _delta(base, v):
    if base is None or v is None or base.total_pnl is None or v.total_pnl is None:
        return None
    return v.total_pnl - base.total_pnl


def format_ablation_markdown(report: AblationReport) -> str:
    """마크다운 리포트(reports/signal_ablation_test.md). 측정 보조 — 매매 미사용."""
    v = report.variants
    base = _get(v, "baseline")
    lines: list[str] = []
    lines.append("# Signal Ablation Test (측정 - 실주문 없음, next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. "
                 "스캐너/디시전/사이징/RiskGate·기본 유니버스·베이스라인 파라미터 미변경.")
    lines.append("")
    lines.append("**mode 범례**: `true-rerun` = run_sim 청산/심볼 플래그만 바꾼 실제 재시뮬. "
                 "`shadow-approx` = 실현 트레이드를 진입 피처로 제거한 근사(진짜 재시뮬 아님, MDD n/a).")
    lines.append("")
    lines.append("| variant | mode | return | total PnL | MDD | ret/MDD | win | trades "
                 "| avg PnL | med PnL | top1 | top3 | best/worst | >SPY | >QQQ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in v:
        lines.append(_row(r))
    lines.append("")

    # PnL delta vs baseline (true-rerun만 의미 있음).
    lines.append("## baseline 대비 PnL 변화 (제거 효과)")
    lines.append("")
    deltas = []
    for r in v:
        if r.name == "baseline":
            continue
        d = _delta(base, r)
        if d is not None:
            deltas.append((r.name, r.mode, d))
            lines.append(f"- {r.name} ({r.mode}): ΔPnL {d:+.2f}")
    lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    true_deltas = [(n, d) for (n, m, d) in deltas if m == MODE_TRUE]
    if true_deltas:
        worst = min(true_deltas, key=lambda x: x[1])    # 가장 큰 PnL 손실 = 가장 중요한 제거
        lines.append(f"- **가장 중요한 컴포넌트?** 제거 시 PnL이 가장 크게 줄어든 변형: "
                     f"{worst[0]} (ΔPnL {worst[1]:+.2f}).")
    no_exit = _get(v, "no_exit_controls")
    if base and no_exit and base.total_pnl is not None and no_exit.total_pnl is not None:
        verdict = "청산이 수익을 깎음" if no_exit.total_pnl > base.total_pnl else "청산이 가치를 더함"
        lines.append(f"- **청산이 가치를 더하나 해치나?** no_exit_controls PnL {no_exit.total_pnl:.0f} "
                     f"vs baseline {base.total_pnl:.0f} → {verdict}.")
    ns = _get(v, "no_stop_loss")
    nt = _get(v, "no_trailing_stop")
    if base and ns and nt:
        ds, dt = _delta(base, ns), _delta(base, nt)
        if ds is not None and dt is not None:
            more = "trailing stop" if dt < ds else "stop loss"
            lines.append(f"- **trailing이 stop loss보다 중요?** no_stop_loss ΔPnL {ds:+.2f}, "
                         f"no_trailing_stop ΔPnL {dt:+.2f} → 더 중요한 쪽: {more}.")
    ntime = _get(v, "no_time_stop")
    if base and ntime and base.total_pnl is not None and ntime.total_pnl is not None:
        verdict = "도움" if ntime.total_pnl < base.total_pnl else "오히려 손해/무관"
        lines.append(f"- **60일 time stop이 돕나?** no_time_stop PnL {ntime.total_pnl:.0f} "
                     f"vs baseline {base.total_pnl:.0f} → time stop은 {verdict}.")
    no_mu = _get(v, "no_MU")
    no_top3 = _get(v, "no_top3_symbols")
    if base and no_mu and base.total_pnl:
        dmu = _delta(base, no_mu)
        msg = f"MU 제거 ΔPnL {dmu:+.2f} ({dmu / base.total_pnl:+.0%})" if dmu is not None else "n/a"
        if no_top3 and (dt3 := _delta(base, no_top3)) is not None:
            msg += f"; top3 제거 ΔPnL {dt3:+.2f} ({dt3 / base.total_pnl:+.0%})"
        lines.append(f"- **MU/top3 의존도?** {msg}.")
    shadow_names = [r.name for r in v if r.mode == MODE_SHADOW]
    true_names = [r.name for r in v if r.mode == MODE_TRUE]
    lines.append(f"- **true-rerun vs shadow?** true-rerun: {', '.join(true_names) or '없음'}. "
                 f"shadow-approx(근사): {', '.join(shadow_names) or '없음'}.")
    lines.append("")

    if base:
        lines.append("## 분기 PnL (baseline)")
        lines.append("")
        for q, pnl in base.quarterly:
            lines.append(f"- {q}: {pnl:.2f}")
        lines.append(f"- 벤치마크: SPY {_pct(base.spy_return)} / QQQ {_pct(base.qqq_return)} / "
                     f"equal-weight {_pct(base.eq_return)}")
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
