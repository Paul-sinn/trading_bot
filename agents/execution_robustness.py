"""실행 정책 로버스트니스 검증 — next-open vs 3% limit이 시간창/심볼/슬리피지에 강건한지 본다(순수 측정).

분기 윈도우 비교 + leave-one-symbol-out + 슬리피지 그리드 + 집중 경고. 라이브/기본 전략/스캐너/디시전/
사이징/RiskGate를 바꾸지 않는다. 갭 가드 미적용. 윈도우/LOO PnL은 사후 근사.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/execution_robustness.md
"""

from __future__ import annotations

from dataclasses import dataclass

_SYMBOL_CONCENTRATION = 0.35   # 한 심볼이 양수 PnL의 35% 초과면 경고.
_WINDOW_CONCENTRATION = 0.5    # 한 윈도우가 양수 수익의 50% 초과면 경고.


@dataclass(frozen=True)
class PolicySummary:
    cumulative_return: float
    max_drawdown: float
    win_rate: float | None
    total_pnl: float
    trades: int


@dataclass(frozen=True)
class WindowCompare:
    label: str
    limit3_return: float | None
    next_open_return: float | None
    limit3_pnl: float | None
    next_open_pnl: float | None
    next_open_wins: bool
    limit3_mdd: float | None
    next_open_mdd: float | None
    trades_limit: int
    trades_next: int


@dataclass(frozen=True)
class LeaveOneOut:
    dropped_symbol: str
    next_open_total_pnl: float
    delta_vs_full: float        # drop − full (음수면 그 심볼이 기여)
    pct_of_full: float | None   # 빼면 잃는 비율(−delta/full)


@dataclass(frozen=True)
class SlippageCompare:
    slippage: float
    limit3_return: float
    next_open_return: float
    next_open_wins: bool


@dataclass(frozen=True)
class RobustnessValidation:
    full_limit3: PolicySummary
    full_next_open: PolicySummary
    windows: tuple[WindowCompare, ...]
    leave_one_out: tuple[LeaveOneOut, ...]
    worst_drop: LeaveOneOut | None
    slippage: tuple[SlippageCompare, ...]
    best_window: WindowCompare | None
    worst_window: WindowCompare | None
    next_open_window_wins: int
    warnings: tuple[str, ...]
    is_robust: bool

    @property
    def real_orders_placed(self) -> int:
        return 0


def compute_window_comparison(limit3_windows, next_open_windows):
    """분기 윈도우별 limit3 vs next-open(윈도우는 .label/.return_pct/.pnl/.max_drawdown/.trade_count)."""
    lim = {w.label: w for w in limit3_windows}
    nxt = {w.label: w for w in next_open_windows}
    out = []
    for lab in sorted(set(lim) | set(nxt)):
        lw, nw = lim.get(lab), nxt.get(lab)
        lr = lw.return_pct if lw else None
        nr = nw.return_pct if nw else None
        out.append(WindowCompare(
            label=lab, limit3_return=lr, next_open_return=nr,
            limit3_pnl=(lw.pnl if lw else None), next_open_pnl=(nw.pnl if nw else None),
            next_open_wins=(nr is not None and lr is not None and nr > lr),
            limit3_mdd=(lw.max_drawdown if lw else None), next_open_mdd=(nw.max_drawdown if nw else None),
            trades_limit=(lw.trade_count if lw else 0), trades_next=(nw.trade_count if nw else 0),
        ))
    return tuple(out)


def compute_leave_one_out(full_next_open_pnl, loo_pnl_by_symbol):
    """심볼별 next-open 재시뮬 총손익으로 의존도(full 대비 delta)를 만든다."""
    out = []
    for sym in sorted(loo_pnl_by_symbol):
        drop = loo_pnl_by_symbol[sym]
        delta = drop - full_next_open_pnl
        pct = (-delta / full_next_open_pnl) if full_next_open_pnl else None
        out.append(LeaveOneOut(dropped_symbol=sym, next_open_total_pnl=drop,
                               delta_vs_full=delta, pct_of_full=pct))
    return tuple(out)


def _policy_total(diag, slippage) -> float:
    return float(sum((l.pnl - l.entry_price * slippage * l.qty)
                     for l in diag.trades if l.pnl is not None))


def compute_slippage_robustness(limit3_diag, next_open_diag, *, slippages, starting_cash):
    """슬리피지별 두 정책 수익률 + next-open 우위 여부."""
    out = []
    for s in slippages:
        lt = _policy_total(limit3_diag, s)
        nt = _policy_total(next_open_diag, s)
        out.append(SlippageCompare(
            slippage=s, limit3_return=lt / starting_cash, next_open_return=nt / starting_cash,
            next_open_wins=(nt > lt),
        ))
    return tuple(out)


def build_validation(limit3_summary, next_open_summary, windows, loo, slippage, next_open_symbol_pnl):
    """검증 결과를 종합한다(경고 + is_robust)."""
    rated = [w for w in windows if w.next_open_return is not None]
    best_window = max(rated, key=lambda w: w.next_open_return) if rated else None
    worst_window = min(rated, key=lambda w: w.next_open_return) if rated else None
    comparable = [w for w in windows if w.next_open_return is not None and w.limit3_return is not None]
    win_wins = sum(1 for w in comparable if w.next_open_wins)

    worst_drop = min(loo, key=lambda l: l.next_open_total_pnl) if loo else None

    warnings: list[str] = []
    pos = {k: v for k, v in next_open_symbol_pnl.items() if v > 0}
    pos_total = sum(pos.values())
    max_share = (max(pos.values()) / pos_total) if pos_total > 0 else 0.0
    if pos_total > 0 and max_share > _SYMBOL_CONCENTRATION:
        top = max(pos, key=lambda s: pos[s])
        warnings.append(f"next-open 양수 PnL의 {max_share:.0%}가 {top} — 35% 초과 집중")

    win_pnls = [w.next_open_pnl for w in windows if w.next_open_pnl is not None and w.next_open_pnl > 0]
    win_pos_total = sum(win_pnls)
    if win_pos_total > 0 and max(win_pnls) / win_pos_total > _WINDOW_CONCENTRATION:
        warnings.append(f"수익이 한 시간창에 집중(최대 윈도우가 {max(win_pnls) / win_pos_total:.0%}) — 국면 의존")

    if len(comparable) >= 3 and win_wins <= 1:
        warnings.append("next-open이 한 좁은 구간에서만 우위 — 시간 강건성 약함")

    slip_robust = all(s.next_open_wins for s in slippage) if slippage else False
    full_win = next_open_summary.cumulative_return > limit3_summary.cumulative_return
    window_majority = len(comparable) > 0 and win_wins >= (len(comparable) + 1) // 2
    broad = max_share <= _SYMBOL_CONCENTRATION
    drop_robust = (worst_drop is None) or (worst_drop.next_open_total_pnl > limit3_summary.total_pnl)
    if not drop_robust and worst_drop is not None:
        warnings.append(f"{worst_drop.dropped_symbol} 제거 시 next-open이 3% limit에 역전 — 단일 심볼 의존")

    is_robust = bool(full_win and window_majority and slip_robust and broad and drop_robust)

    return RobustnessValidation(
        full_limit3=limit3_summary, full_next_open=next_open_summary, windows=tuple(windows),
        leave_one_out=tuple(loo), worst_drop=worst_drop, slippage=tuple(slippage),
        best_window=best_window, worst_window=worst_window, next_open_window_wins=win_wins,
        warnings=tuple(warnings), is_robust=is_robust,
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_robustness_validation(report: RobustnessValidation) -> str:
    """사람이 읽는 실행 로버스트니스 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 96]
    lines.append("Execution Robustness Validation (측정 - 실주문 없음, next-open vs 3% limit)")
    lines.append("=" * 96)
    lim, nxt = report.full_limit3, report.full_next_open
    lines.append(
        f"full-period: 3% limit cum {_fmt(lim.cumulative_return)} MDD {_fmt(lim.max_drawdown)} "
        f"pnl {lim.total_pnl:.2f}  |  next-open cum {_fmt(nxt.cumulative_return)} "
        f"MDD {_fmt(nxt.max_drawdown)} pnl {nxt.total_pnl:.2f}"
    )

    lines.append("by window (quarter):")
    lines.append(f"  {'window':<10}{'limit3':>9}{'nextOpen':>10}{'nextWins':>9}{'trd_l/n':>10}")
    for w in report.windows:
        lines.append(
            f"  {w.label:<10}{_fmt(w.limit3_return):>9}{_fmt(w.next_open_return):>10}"
            f"{('Y' if w.next_open_wins else 'n'):>9}{f'{w.trades_limit}/{w.trades_next}':>10}"
        )
    lines.append(f"next-open wins {report.next_open_window_wins}/{len(report.windows)} windows")
    if report.best_window:
        lines.append(f"best window (next-open): {report.best_window.label} ({_fmt(report.best_window.next_open_return)})")
    if report.worst_window:
        lines.append(f"worst window (next-open): {report.worst_window.label} ({_fmt(report.worst_window.next_open_return)})")

    lines.append("slippage robustness (next-open wins?):")
    for s in report.slippage:
        lines.append(
            f"  slip {s.slippage:.2%}: limit3 {_fmt(s.limit3_return)} vs next-open {_fmt(s.next_open_return)} "
            f"-> {'next-open' if s.next_open_wins else '3% limit'}"
        )

    if report.leave_one_out:
        lines.append("leave-one-symbol-out (next-open total pnl when dropped):")
        ordered = sorted(report.leave_one_out, key=lambda l: l.next_open_total_pnl)
        for l in ordered[:12]:
            lines.append(
                f"  drop {l.dropped_symbol:<8} pnl {l.next_open_total_pnl:>10.2f}  "
                f"Δfull {l.delta_vs_full:>+10.2f}  ({_fmt(l.pct_of_full, '{:+.0%}')})"
            )
        if report.worst_drop:
            lines.append(f"worst drop: {report.worst_drop.dropped_symbol} -> pnl {report.worst_drop.next_open_total_pnl:.2f}")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")
    lines.append(f"VERDICT: next-open advantage is {'ROBUST' if report.is_robust else 'FRAGILE / not confirmed'}")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 96)
    return "\n".join(lines)
