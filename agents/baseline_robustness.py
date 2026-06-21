"""현실 베이스라인 전략 강건성 리포트 — 잠긴 next-bar-limit 3%에서 전략 전체가 강건한지 본다(순수 측정).

기존 시뮬 산출물(performance + trade_diagnostics leg)과 robustness_report/baseline_comparison
빌딩블록을 묶어 풀기간/윈도우/LOO/벤치마크/슬리피지/집중/청산사유를 측정한다. 상태/매매/veto/전략을
바꾸지 않는다. 갭 가드 미적용. winner extension 미적용. next-open 미사용.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/baseline_robustness.md
"""

from __future__ import annotations

from dataclasses import dataclass

# 청산 사유 → 리포트 버킷.
_EXIT_BUCKETS = {
    "time_stop": "time_stop",
    "trailing_stop_hit": "trailing_stop",
    "stop_loss_hit": "stop_loss",
}
_TOP1_SHARE = 0.35     # top 심볼이 양수 PnL의 35% 초과면 경고.
_TOP3_SHARE = 0.65     # top 3 심볼이 양수 PnL의 65% 초과면 경고.
_COLLAPSE_DROP = 0.5   # worst 심볼 제거가 총손익을 50%+ 떨어뜨리면 붕괴 경고.


@dataclass(frozen=True)
class BaselineFull:
    cumulative_return: float
    max_drawdown: float
    win_rate: float | None
    total_pnl: float
    trades: int
    return_over_mdd: float | None


@dataclass(frozen=True)
class SlippageStress:
    slippage: float
    total_pnl: float
    return_pct: float


@dataclass(frozen=True)
class ExitReasonStat:
    reason: str
    count: int
    total_pnl: float
    share_of_trades: float


@dataclass(frozen=True)
class Concentration:
    top_symbol: str | None
    top1_share: float | None
    top3_symbols: tuple[str, ...]
    top3_share: float | None
    worst_symbol: str | None
    worst_removal_pnl: float | None     # worst 심볼 제외 시 남는 총손익(근사)
    worst_removal_delta: float | None   # 제외로 잃는 손익(음수면 기여)


@dataclass(frozen=True)
class BaselineRobustness:
    full: BaselineFull
    robustness: object                  # RobustnessReport (windows/symbol/LOO 재사용)
    benchmark: object                   # BaselineComparison (SPY/QQQ/equal-weight 재사용)
    slippage: tuple[SlippageStress, ...]
    concentration: Concentration
    exit_reasons: tuple[ExitReasonStat, ...]
    best_window: object | None
    worst_window: object | None
    beats_spy: bool | None
    beats_qqq: bool | None
    survives_slippage: bool
    window_positive_share: float | None
    warnings: tuple[str, ...]
    is_robust: bool

    @property
    def real_orders_placed(self) -> int:
        return 0


def compute_full_result(performance) -> BaselineFull:
    """performance에서 풀기간 요약 + return/MDD 비율."""
    ret = float(performance.cumulative_return)
    mdd = float(performance.max_drawdown)
    return BaselineFull(
        cumulative_return=ret, max_drawdown=mdd,
        win_rate=getattr(performance, "win_rate", None),
        total_pnl=float(performance.total_pnl), trades=int(performance.num_trades),
        return_over_mdd=(ret / mdd if mdd > 0 else None),
    )


def compute_slippage_stress(diag, *, slippages, starting_cash) -> tuple[SlippageStress, ...]:
    """단일 정책 슬리피지 스트레스(adj_pnl = pnl − entry×slip×qty). 원본 diag 미변형."""
    out: list[SlippageStress] = []
    legs = [l for l in diag.trades if l.pnl is not None]
    for s in slippages:
        total = float(sum(l.pnl - l.entry_price * s * l.qty for l in legs))
        out.append(SlippageStress(slippage=s, total_pnl=total,
                                  return_pct=(total / starting_cash if starting_cash else 0.0)))
    return tuple(out)


def compute_exit_reason_distribution(diag) -> tuple[ExitReasonStat, ...]:
    """청산 사유를 time_stop/trailing_stop/stop_loss/other로 묶는다(미청산 OPEN 제외)."""
    agg: dict[str, list] = {}   # bucket -> [count, pnl_sum]
    closed = [l for l in diag.trades if l.exit_reason not in (None, "OPEN")]
    for l in closed:
        bucket = _EXIT_BUCKETS.get(l.exit_reason, "other")
        rec = agg.setdefault(bucket, [0, 0.0])
        rec[0] += 1
        rec[1] += (l.pnl or 0.0)
    total = len(closed)
    order = ["time_stop", "trailing_stop", "stop_loss", "other"]
    out: list[ExitReasonStat] = []
    for bucket in order:
        if bucket in agg:
            c, pnl = agg[bucket]
            out.append(ExitReasonStat(reason=bucket, count=c, total_pnl=pnl,
                                      share_of_trades=(c / total if total else 0.0)))
    return tuple(out)


def compute_concentration(symbol_totals: dict[str, float]) -> Concentration:
    """심볼별 총손익 dict로 top1/top3 share + worst 심볼 제거 영향."""
    if not symbol_totals:
        return Concentration(None, None, (), None, None, None, None)
    pos = {k: v for k, v in symbol_totals.items() if v > 0}
    pos_total = sum(pos.values())
    ranked = sorted(pos, key=lambda s: pos[s], reverse=True)
    top_symbol = ranked[0] if ranked else None
    top3 = tuple(ranked[:3])
    top1_share = (pos[top_symbol] / pos_total) if (top_symbol and pos_total > 0) else None
    top3_share = (sum(pos[s] for s in top3) / pos_total) if (top3 and pos_total > 0) else None

    total = sum(symbol_totals.values())
    # worst removal = 빼면 가장 손해인(=가장 기여한) 심볼.
    worst_symbol = max(symbol_totals, key=lambda s: symbol_totals[s])
    worst_removal_pnl = total - symbol_totals[worst_symbol]
    worst_removal_delta = -symbol_totals[worst_symbol]
    return Concentration(
        top_symbol=top_symbol, top1_share=top1_share, top3_symbols=top3, top3_share=top3_share,
        worst_symbol=worst_symbol, worst_removal_pnl=worst_removal_pnl,
        worst_removal_delta=worst_removal_delta,
    )


def _baseline_return(benchmark, name_prefix):
    for b in getattr(benchmark, "baselines", ()):
        if b.name.startswith(name_prefix):
            return b.cumulative_return
    return None


def build_baseline_robustness(full, robustness, benchmark, slippage, exit_reasons, concentration):
    """강건성 묶음을 종합한다(서브 플래그 + 경고 + is_robust)."""
    windows = getattr(robustness, "windows", ())
    rated = [w for w in windows if w.return_pct is not None]
    pos_windows = sum(1 for w in rated if w.return_pct > 0)
    window_positive_share = (pos_windows / len(rated)) if rated else None

    spy_ret = _baseline_return(benchmark, "SPY")
    qqq_ret = _baseline_return(benchmark, "QQQ")
    beats_spy = (full.cumulative_return > spy_ret) if spy_ret is not None else None
    beats_qqq = (full.cumulative_return > qqq_ret) if qqq_ret is not None else None

    survives_slippage = bool(slippage) and all(s.total_pnl > 0 for s in slippage)

    warnings: list[str] = []
    if concentration.top1_share is not None and concentration.top1_share > _TOP1_SHARE:
        warnings.append(
            f"top 심볼 {concentration.top_symbol}이 양수 PnL의 {concentration.top1_share:.0%} — 35% 초과 집중"
        )
    if concentration.top3_share is not None and concentration.top3_share > _TOP3_SHARE:
        warnings.append(f"top 3 심볼이 양수 PnL의 {concentration.top3_share:.0%} — 소수 종목 집중")
    if full.total_pnl > 0 and concentration.worst_removal_delta is not None:
        drop_frac = -concentration.worst_removal_delta / full.total_pnl
        if drop_frac >= _COLLAPSE_DROP:
            warnings.append(
                f"{concentration.worst_symbol} 제거 시 총손익 {drop_frac:.0%} 붕괴 — 단일 심볼 의존"
            )
    if not survives_slippage:
        warnings.append("슬리피지 스트레스에서 총손익이 음수로 전환 — 비용 취약")
    if beats_spy is False:
        warnings.append("현실 진입 후 SPY 매수보유에 미달 — 알파 의문")

    broad = (concentration.top1_share is None) or (concentration.top1_share <= _TOP1_SHARE)
    no_collapse = not any("붕괴" in w for w in warnings)
    window_robust = (window_positive_share is not None) and (window_positive_share >= 0.5)
    is_robust = bool(
        broad and no_collapse and survives_slippage and window_robust and (beats_spy is not False)
    )

    return BaselineRobustness(
        full=full, robustness=robustness, benchmark=benchmark, slippage=tuple(slippage),
        concentration=concentration, exit_reasons=tuple(exit_reasons),
        best_window=getattr(robustness, "best_window", None),
        worst_window=getattr(robustness, "worst_window", None),
        beats_spy=beats_spy, beats_qqq=beats_qqq, survives_slippage=survives_slippage,
        window_positive_share=window_positive_share, warnings=tuple(warnings), is_robust=is_robust,
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_baseline_robustness(report: BaselineRobustness) -> str:
    """사람이 읽는 베이스라인 강건성 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    f = report.full
    lines: list[str] = ["=" * 96]
    lines.append("Realistic Baseline Robustness (측정 - 실주문 없음, next-bar-limit 3% 잠금)")
    lines.append("=" * 96)
    lines.append(
        f"full-period: return {_fmt(f.cumulative_return)} MDD {_fmt(f.max_drawdown)} "
        f"win {_fmt(f.win_rate)} pnl {f.total_pnl:.2f} trades {f.trades} "
        f"ret/MDD {_fmt(f.return_over_mdd, '{:.2f}')}"
    )

    lines.append("by window (quarter):")
    lines.append(f"  {'window':<10}{'return':>9}{'mdd':>9}{'pnl':>11}{'trades':>8}")
    for w in getattr(report.robustness, "windows", ()):
        lines.append(
            f"  {w.label:<10}{_fmt(w.return_pct):>9}{_fmt(w.max_drawdown):>9}"
            f"{w.pnl:>11.2f}{w.trade_count:>8}"
        )
    if report.best_window:
        lines.append(f"best window : {report.best_window.label} ({_fmt(report.best_window.return_pct)})")
    if report.worst_window:
        lines.append(f"worst window: {report.worst_window.label} ({_fmt(report.worst_window.return_pct)})")
    lines.append(f"windows positive: {_fmt(report.window_positive_share)}")

    lines.append("leave-one-symbol-out (rerun where available):")
    lines.append(f"  {'excluded':<10}{'total_pnl':>12}{'Δtotal':>11}{'return':>9}{'mode':>13}")
    loo = sorted(getattr(report.robustness, "leave_one_out", ()), key=lambda l: l.total_pnl)
    for l in loo[:12]:
        lines.append(
            f"  {l.excluded_symbol:<10}{l.total_pnl:>12.2f}{l.total_pnl_diff:>+11.2f}"
            f"{_fmt(l.return_pct):>9}{l.mode:>13}"
        )

    c = report.concentration
    lines.append(
        f"concentration: top {c.top_symbol} {_fmt(c.top1_share, '{:.0%}')}  "
        f"top3 {','.join(c.top3_symbols)} {_fmt(c.top3_share, '{:.0%}')}  "
        f"worst-drop {c.worst_symbol} Δ{_fmt(c.worst_removal_delta, '{:+.2f}')}"
    )

    lines.append("benchmark (buy-hold):")
    for b in getattr(report.benchmark, "baselines", ()):
        label = b.name + (f" [{b.symbol}]" if b.symbol else "")
        if b.cumulative_return is None:
            lines.append(f"  {label:<26} ({b.note})")
        else:
            lines.append(
                f"  {label:<26}{_fmt(b.cumulative_return):>9}  "
                f"Δret(strat-base) {_fmt(b.return_diff_vs_strategy, '{:+.2%}')}"
            )
    lines.append(
        f"beats SPY: {report.beats_spy}   beats QQQ: {report.beats_qqq}"
    )

    lines.append("slippage stress (single policy):")
    for s in report.slippage:
        lines.append(f"  slip {s.slippage:.2%}: pnl {s.total_pnl:>9.2f}  return {_fmt(s.return_pct)}")
    lines.append(f"survives slippage: {report.survives_slippage}")

    lines.append("exit reason distribution:")
    for e in report.exit_reasons:
        lines.append(
            f"  {e.reason:<14} count {e.count:>4}  pnl {e.total_pnl:>9.2f}  share {_fmt(e.share_of_trades, '{:.0%}')}"
        )

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")
    lines.append(f"VERDICT: baseline is {'ROBUST' if report.is_robust else 'FRAGILE / not confirmed'}")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 96)
    return "\n".join(lines)
