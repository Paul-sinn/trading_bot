"""진입 실행 라우팅 진단 — 심볼 갭 행태로 3% limit / next-open을 라우팅했다면의 what-if(순수 측정).

두 실행(3% limit / next-open)의 심볼별 PnL + price_data 갭 통계만 읽는다. 라우팅 total은 심볼별 PnL을
합친 **what-if 근사**(단일 $현금 포트폴리오 시뮬 아님). 라이브/기본 전략/스캐너/디시전/사이징/RiskGate를
바꾸지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/진단 전용 — 동작 변경 없음(읽기만).

spec: specs/entry_routing.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

_CONCENTRATION_SHARE = 0.6


@dataclass(frozen=True)
class GapStats:
    """심볼의 진입-바 갭(open/직전종가 − 1) 통계."""

    avg_gap: float | None
    median_gap: float | None
    gap_up_freq: float | None
    large_gap_up_freq_2pct: float | None
    large_gap_up_freq_3pct: float | None
    n: int


@dataclass(frozen=True)
class SymbolRouting:
    """심볼별 라우팅 진단."""

    symbol: str
    limit3_pnl: float
    next_open_pnl: float
    diff: float                       # next_open − limit3
    gap: GapStats
    is_high_gap: bool
    prefers: str                      # "next_open" / "limit"
    missed_profitable_count: int
    missed_profitable_pnl: float


@dataclass(frozen=True)
class RoutedPolicyResult:
    """라우팅 정책의 what-if 메트릭(근사 — 단일 포트폴리오 시뮬 아님)."""

    name: str
    total_pnl: float
    cumulative_return: float
    trades: int
    win_rate: float | None
    max_drawdown_proxy: float | None
    return_mdd_ratio: float | None
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    chosen_routes: dict
    is_diagnostic_only: bool = False

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class RoutingReport:
    """진입 실행 라우팅 진단 묶음. real_orders_placed는 항상 0."""

    symbols: tuple[SymbolRouting, ...]
    policies: tuple[RoutedPolicyResult, ...]
    starting_cash: float
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def compute_symbol_gap_stats(df, entry_dates) -> GapStats:
    """진입일별 갭(open/직전 종가 − 1)을 모아 통계를 낸다. df/날짜 결측은 건너뛴다(안전)."""
    gaps: list[float] = []
    if df is not None and {"open", "close"}.issubset(getattr(df, "columns", [])):
        for d in entry_dates:
            try:
                ts = pd.Timestamp(d)
            except (ValueError, TypeError):
                continue
            if ts not in df.index:
                continue
            pos = df.index.get_loc(ts)
            if not isinstance(pos, int) or pos < 1:
                continue
            prev_close = float(df["close"].iloc[pos - 1])
            op = float(df["open"].iloc[pos])
            if prev_close > 0:
                gaps.append(op / prev_close - 1.0)
    if not gaps:
        return GapStats(None, None, None, None, None, 0)
    n = len(gaps)
    return GapStats(
        avg_gap=statistics.fmean(gaps), median_gap=statistics.median(gaps),
        gap_up_freq=sum(1 for g in gaps if g > 0) / n,
        large_gap_up_freq_2pct=sum(1 for g in gaps if g > 0.02) / n,
        large_gap_up_freq_3pct=sum(1 for g in gaps if g > 0.03) / n,
        n=n,
    )


def _by_symbol(diag):
    """symbol → {pnl, trades, wins, legs}."""
    out: dict[str, dict] = {}
    for l in diag.trades:
        if l.pnl is None:
            continue
        r = out.setdefault(l.symbol, {"pnl": 0.0, "trades": 0, "wins": 0, "legs": []})
        r["pnl"] += l.pnl
        r["trades"] += 1
        if l.pnl > 0:
            r["wins"] += 1
        r["legs"].append(l)
    return out


def _mdd_proxy(legs) -> float | None:
    """청산일순 누적 실현손익의 최대 낙폭(달러 근사)."""
    dated = [l for l in legs if l.exit_date is not None and l.pnl is not None]
    if not dated:
        return None
    dated = sorted(dated, key=lambda l: l.exit_date)
    cum = peak = mdd = 0.0
    for l in dated:
        cum += l.pnl
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def _routed_metrics(name, chosen_legs, routes, starting_cash, *, diagnostic=False) -> RoutedPolicyResult:
    total = float(sum(l.pnl for l in chosen_legs if l.pnl is not None))
    closed = [l for l in chosen_legs if l.exit_date is not None and l.pnl is not None]
    wins = sum(1 for l in closed if l.pnl > 0)
    win_rate = (wins / len(closed)) if closed else None
    mdd = _mdd_proxy(chosen_legs)
    cum = total / starting_cash if starting_cash > 0 else 0.0
    ratio = (cum / (mdd / starting_cash)) if (mdd and mdd > 0 and starting_cash > 0) else None

    pnl_by: dict[str, float] = {}
    for l in chosen_legs:
        if l.pnl is not None:
            pnl_by[l.symbol] = pnl_by.get(l.symbol, 0.0) + l.pnl
    pos_total = sum(v for v in pnl_by.values() if v > 0)
    top_sym = max(pnl_by, key=lambda s: pnl_by[s]) if pnl_by else None
    share = (pnl_by[top_sym] / pos_total) if (top_sym and pos_total > 0 and pnl_by[top_sym] > 0) else None

    return RoutedPolicyResult(
        name=name, total_pnl=total, cumulative_return=cum, trades=len(chosen_legs),
        win_rate=win_rate, max_drawdown_proxy=mdd, return_mdd_ratio=ratio,
        top_symbol=top_sym, top_symbol_pnl_share=share, chosen_routes=dict(routes),
        is_diagnostic_only=diagnostic,
    )


def compute_entry_routing(
    limit3_diag, next_open_diag, price_data, *, starting_cash: float = 1000.0,
    high_gap_threshold: float = 0.25,
) -> RoutingReport:
    """심볼 갭 행태로 진입 실행을 라우팅한 what-if을 산출한다(읽기 전용 — 입력 불변)."""
    lim = _by_symbol(limit3_diag)
    nxt = _by_symbol(next_open_diag)
    symbols = sorted(set(lim) | set(nxt))
    price_data = price_data or {}

    sym_rows: list[SymbolRouting] = []
    for s in symbols:
        l = lim.get(s, {"pnl": 0.0, "trades": 0, "wins": 0, "legs": []})
        n = nxt.get(s, {"pnl": 0.0, "trades": 0, "wins": 0, "legs": []})
        entries = sorted({leg.entry_date for leg in n["legs"]} | {leg.entry_date for leg in l["legs"]})
        gap = compute_symbol_gap_stats(price_data.get(s), entries)
        is_high = (gap.large_gap_up_freq_2pct is not None
                   and gap.large_gap_up_freq_2pct >= high_gap_threshold)
        diff = n["pnl"] - l["pnl"]
        n_wins = n["wins"]
        l_wins = l["wins"]
        sym_rows.append(SymbolRouting(
            symbol=s, limit3_pnl=l["pnl"], next_open_pnl=n["pnl"], diff=diff, gap=gap,
            is_high_gap=is_high, prefers=("next_open" if diff > 0 else "limit"),
            missed_profitable_count=max(0, n_wins - l_wins),
            missed_profitable_pnl=max(0.0, diff),
        ))

    def _legs_for(s, route):
        src = nxt if route == "next_open" else lim
        return src.get(s, {"legs": []})["legs"]

    def _build(name, route_fn, *, diagnostic=False):
        routes = {s: route_fn(r) for s, r in ((sr.symbol, sr) for sr in sym_rows)}
        legs = [leg for s, route in routes.items() for leg in _legs_for(s, route)]
        return _routed_metrics(name, legs, routes, starting_cash, diagnostic=diagnostic)

    policies = (
        _build("all_limit_3pct", lambda r: "limit"),
        _build("all_next_open", lambda r: "next_open"),
        _build("gap_routed_conservative", lambda r: "next_open" if r.is_high_gap else "limit"),
        _build("gap_routed_aggressive", lambda r: "next_open" if r.diff > 0 else "limit", diagnostic=True),
    )

    warnings: list[str] = [
        "gap_routed_aggressive는 진단 전용(overfit 위험) — 사후 승자(diff>0) 선택"
    ]
    pos_diffs = [sr.diff for sr in sym_rows if sr.diff > 0]
    if pos_diffs:
        total_pos = sum(pos_diffs)
        top = max(sym_rows, key=lambda sr: sr.diff)
        if top.diff > 0 and total_pos > 0 and top.diff / total_pos >= _CONCENTRATION_SHARE:
            warnings.append(
                f"라우팅 이득이 {top.symbol}에 집중({top.diff / total_pos:.0%}) — 단일 심볼 의존"
            )

    return RoutingReport(
        symbols=tuple(sym_rows), policies=policies, starting_cash=starting_cash,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_entry_routing(report: RoutingReport) -> str:
    """사람이 읽는 진입 실행 라우팅 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 100]
    lines.append("Entry Execution Routing (측정 - 실주문 없음, what-if 근사 — 단일 포트폴리오 시뮬 아님)")
    lines.append("=" * 100)
    lines.append(
        f"  {'symbol':<8}{'avg_gap':>9}{'gapUp%':>8}{'>2%':>7}{'>3%':>7}{'limit3_pnl':>12}"
        f"{'nextO_pnl':>12}{'diff':>10}{'highGap':>8}{'prefers':>10}"
    )
    for s in report.symbols:
        g = s.gap
        lines.append(
            f"  {s.symbol:<8}{_fmt(g.avg_gap):>9}{_fmt(g.gap_up_freq, '{:.0%}'):>8}"
            f"{_fmt(g.large_gap_up_freq_2pct, '{:.0%}'):>7}{_fmt(g.large_gap_up_freq_3pct, '{:.0%}'):>7}"
            f"{s.limit3_pnl:>12.2f}{s.next_open_pnl:>12.2f}{s.diff:>+10.2f}"
            f"{('Y' if s.is_high_gap else 'n'):>8}{s.prefers:>10}"
        )

    lines.append(
        f"  {'policy':<26}{'cum_ret':>9}{'MDDx':>10}{'win':>7}{'total_PnL':>12}{'trades':>7}"
        f"{'ret/MDD':>8}{'topShare':>9}"
    )
    for p in report.policies:
        tag = "  [diagnostic-only]" if p.is_diagnostic_only else ""
        lines.append(
            f"  {p.name:<26}{_fmt(p.cumulative_return):>9}{_fmt(p.max_drawdown_proxy, '{:.2f}'):>10}"
            f"{_fmt(p.win_rate):>7}{p.total_pnl:>12.2f}{p.trades:>7}"
            f"{_fmt(p.return_mdd_ratio, '{:.2f}'):>8}{_fmt(p.top_symbol_pnl_share, '{:.0%}'):>9}{tag}"
        )

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 100)
    return "\n".join(lines)
