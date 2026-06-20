"""강건성/안정성 리포트 — 성과가 심볼·기간에 걸쳐 강건한지 본다(순수 측정).

기존 시뮬 산출물(trade_diagnostics의 트레이드 leg + equity 곡선)만 읽는다. 상태/매매/veto를 바꾸지
않고, 섀도 점수 필터를 실 트레이드에 적용하지 않는다. "한 심볼/한 분기가 수익을 다 설명하나?"를 본다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/robustness_report.md
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.trade_diagnostics import compute_trade_diagnostics

_CONCENTRATION_SHARE = 0.5    # 양수 손익의 50% 초과를 한 심볼이 차지하면 집중 경고.
_COLLAPSE_DROP = 0.5          # top contributor 제거가 총손익을 50%+ 떨어뜨리면 붕괴 경고.
_MIN_TRADES = 4
_MIN_WINDOWS = 2


@dataclass(frozen=True)
class WindowStat:
    """분기 윈도우 성과(equity 곡선 기반)."""

    label: str
    start_date: str | None
    end_date: str | None
    start_equity: float
    end_equity: float
    return_pct: float | None
    pnl: float
    max_drawdown: float | None
    trade_count: int


@dataclass(frozen=True)
class SymbolPerf:
    """심볼별 성과(실현+미실현)."""

    symbol: str
    trades: int
    total_pnl: float
    win_rate: float | None


@dataclass(frozen=True)
class LeaveOneOut:
    """한 심볼 제외 결과. mode='rerun'(실 재시뮬) 또는 'trade-removal'(손익 제거 근사)."""

    excluded_symbol: str
    total_pnl: float
    total_pnl_diff: float
    return_pct: float | None
    max_drawdown: float | None
    mode: str


@dataclass(frozen=True)
class RobustnessReport:
    """강건성 분석 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    windows: tuple[WindowStat, ...]
    best_window: WindowStat | None
    worst_window: WindowStat | None
    symbol_perf: tuple[SymbolPerf, ...]
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    leave_one_out: tuple[LeaveOneOut, ...]
    actual_total_pnl: float
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _quarter(date_str: str | None) -> str | None:
    """'YYYY-MM-DD' → 'YYYY-Qn'. 파싱 불가 시 None."""
    if not date_str or len(date_str) < 7:
        return None
    try:
        year = int(date_str[:4])
        month = int(date_str[5:7])
    except ValueError:
        return None
    return f"{year}-Q{(month - 1) // 3 + 1}"


def _final_marks(price_data) -> dict[str, float]:
    """price_data에서 심볼별 마지막 종가(미청산 마크). 없으면 빈 dict."""
    marks: dict[str, float] = {}
    if not price_data:
        return marks
    for sym, df in price_data.items():
        try:
            if len(df) > 0:
                marks[sym] = float(df["close"].iloc[-1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return marks


def _window_mdd(equities: list[float]) -> float | None:
    """윈도우 내 equity의 최대 낙폭(비율)."""
    peak = float("-inf")
    mdd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd if equities else None


def _windows(equity_over_time, trades) -> list[WindowStat]:
    """equity 곡선을 분기로 묶어 윈도우 통계를 만든다."""
    grouped: dict[str, list[tuple[str, float]]] = {}
    for date, eq in equity_over_time:
        q = _quarter(date)
        if q is None:
            continue
        grouped.setdefault(q, []).append((date, eq))

    entries_by_q: dict[str, int] = {}
    for t in trades:
        q = _quarter(t.entry_date)
        if q is not None:
            entries_by_q[q] = entries_by_q.get(q, 0) + 1

    out: list[WindowStat] = []
    for q in sorted(grouped):
        pts = grouped[q]
        eqs = [e for _, e in pts]
        start_eq, end_eq = eqs[0], eqs[-1]
        ret = (end_eq / start_eq - 1.0) if start_eq > 0 else None
        out.append(WindowStat(
            label=q, start_date=pts[0][0], end_date=pts[-1][0],
            start_equity=start_eq, end_equity=end_eq, return_pct=ret,
            pnl=end_eq - start_eq, max_drawdown=_window_mdd(eqs),
            trade_count=entries_by_q.get(q, 0),
        ))
    return out


def _symbol_perf(trades) -> tuple[list[SymbolPerf], dict[str, float]]:
    """심볼별 성과 + 심볼별 총손익 dict(priced leg만)."""
    agg: dict[str, list] = {}   # symbol -> [count, total, wins]
    for t in trades:
        if t.pnl is None:
            continue
        rec = agg.setdefault(t.symbol, [0, 0.0, 0])
        rec[0] += 1
        rec[1] += t.pnl
        if t.pnl > 0:
            rec[2] += 1
    perf = [
        SymbolPerf(symbol=s, trades=c, total_pnl=tot, win_rate=(w / c if c else None))
        for s, (c, tot, w) in agg.items()
    ]
    perf.sort(key=lambda p: p.total_pnl, reverse=True)
    totals = {p.symbol: p.total_pnl for p in perf}
    return perf, totals


def compute_robustness_report(
    multiday,
    price_data,
    *,
    trade_diag=None,
    rerun_results=None,
) -> RobustnessReport:
    """심볼·기간 강건성을 점검한다(읽기 전용 — 입력 불변).

    trade_diag를 주면 그것을, 아니면 multiday+price_data로 trade_diagnostics를 계산한다.
    rerun_results({제외심볼: HistoricalResult})가 있으면 해당 심볼은 실제 LOO 재시뮬 결과를 쓴다.
    """
    if trade_diag is None:
        trade_diag = compute_trade_diagnostics(multiday, final_prices=_final_marks(price_data))

    priced = [t for t in trade_diag.trades if t.pnl is not None]
    actual_total = float(sum(t.pnl for t in priced))

    windows = _windows(trade_diag.equity_over_time, trade_diag.trades)
    ranked = [w for w in windows if w.return_pct is not None]
    best_window = max(ranked, key=lambda w: w.return_pct) if ranked else None
    worst_window = min(ranked, key=lambda w: w.return_pct) if ranked else None

    symbol_perf, sym_totals = _symbol_perf(priced)

    pos_total = sum(v for v in sym_totals.values() if v > 0)
    top_symbol = symbol_perf[0].symbol if symbol_perf else None
    if top_symbol is not None and pos_total > 0 and sym_totals[top_symbol] > 0:
        top_share = sym_totals[top_symbol] / pos_total
    else:
        top_share = None

    rerun_results = rerun_results or {}
    leave_one_out: list[LeaveOneOut] = []
    for sym in sorted(sym_totals):
        if sym in rerun_results:
            perf = getattr(rerun_results[sym], "performance", None)
            total = float(getattr(perf, "total_pnl", actual_total - sym_totals[sym]))
            leave_one_out.append(LeaveOneOut(
                excluded_symbol=sym, total_pnl=total, total_pnl_diff=total - actual_total,
                return_pct=getattr(perf, "cumulative_return", None),
                max_drawdown=getattr(perf, "max_drawdown", None), mode="rerun",
            ))
        else:
            total = actual_total - sym_totals[sym]
            leave_one_out.append(LeaveOneOut(
                excluded_symbol=sym, total_pnl=total, total_pnl_diff=-sym_totals[sym],
                return_pct=None, max_drawdown=None, mode="trade-removal",
            ))

    warnings: list[str] = []
    if top_share is not None and top_share > _CONCENTRATION_SHARE:
        warnings.append(f"{top_symbol}이 양수 손익의 {top_share:.0%} 차지 — 단일 심볼 집중")
    if actual_total > 0 and top_symbol is not None:
        top_loo = next((l for l in leave_one_out if l.excluded_symbol == top_symbol), None)
        if top_loo is not None:
            drop_frac = -top_loo.total_pnl_diff / actual_total
            if drop_frac >= _COLLAPSE_DROP:
                warnings.append(
                    f"{top_symbol} 제거 시 총손익 {drop_frac:.0%} 붕괴 — 성과가 한 심볼에 의존"
                )
    if len(priced) < _MIN_TRADES:
        warnings.append(f"표본 부족(trades={len(priced)} < {_MIN_TRADES}) — 강건성 신뢰도 낮음")
    if len(windows) < _MIN_WINDOWS:
        warnings.append(f"기간 부족(windows={len(windows)} < {_MIN_WINDOWS}) — 안정성 판단 제한")

    return RobustnessReport(
        windows=tuple(windows),
        best_window=best_window,
        worst_window=worst_window,
        symbol_perf=tuple(symbol_perf),
        top_symbol=top_symbol,
        top_symbol_pnl_share=top_share,
        leave_one_out=tuple(leave_one_out),
        actual_total_pnl=actual_total,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_robustness_report(report: RobustnessReport) -> str:
    """사람이 읽는 강건성 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Robustness / Stability (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)
    lines.append(f"actual total pnl: {report.actual_total_pnl:.2f}")

    lines.append("by window (quarter):")
    lines.append(f"  {'window':<10}{'return':>9}{'pnl':>11}{'mdd':>9}{'trades':>8}")
    for w in report.windows:
        lines.append(
            f"  {w.label:<10}{_fmt(w.return_pct, '{:.2%}'):>9}{_fmt(w.pnl):>11}"
            f"{_fmt(w.max_drawdown, '{:.2%}'):>9}{w.trade_count:>8}"
        )
    if report.best_window is not None:
        lines.append(
            f"best window : {report.best_window.label} ({_fmt(report.best_window.return_pct, '{:.2%}')})"
        )
    if report.worst_window is not None:
        lines.append(
            f"worst window: {report.worst_window.label} ({_fmt(report.worst_window.return_pct, '{:.2%}')})"
        )

    lines.append("by symbol:")
    lines.append(f"  {'symbol':<8}{'trades':>7}{'total_pnl':>12}{'win_rate':>10}")
    for s in report.symbol_perf:
        lines.append(
            f"  {s.symbol:<8}{s.trades:>7}{s.total_pnl:>12.2f}{_fmt(s.win_rate, '{:.0%}'):>10}"
        )
    lines.append(
        f"top symbol: {report.top_symbol or '(none)'}  "
        f"positive-pnl share: {_fmt(report.top_symbol_pnl_share, '{:.0%}')}"
    )

    lines.append("leave-one-symbol-out:")
    lines.append(f"  {'excluded':<10}{'total_pnl':>12}{'Δtotal':>11}{'mode':>14}")
    for l in report.leave_one_out:
        lines.append(
            f"  {l.excluded_symbol:<10}{l.total_pnl:>12.2f}{l.total_pnl_diff:>+11.2f}{l.mode:>14}"
        )

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
