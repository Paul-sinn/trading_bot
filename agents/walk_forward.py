"""장기/워크포워드 검증 — 잠긴 next-bar-limit 3% 베이스라인을 날짜 윈도우에 걸쳐 본다(순수 측정).

윈도우 생성·요약·포맷은 순수 함수. 윈도우별 재시뮬은 러너(scripts/walk_forward.py)가 run_sim으로 한다.
상태/매매/veto/전략을 바꾸지 않는다. 갭 가드 미적용. winner extension 미적용. next-open 미사용.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/walk_forward.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_BAD_WINDOW_RETURN = -0.10     # 윈도우 수익률 −10% 이하면 "크게 손실" 경고.
_REGIME_SHARE = 0.6            # 한 윈도우가 양수 수익의 60% 초과면 한 국면 집중 경고.
_BULL_YEARS = frozenset({2025, 2026})


@dataclass(frozen=True)
class Window:
    label: str
    kind: str          # full | year | roll6 | roll12
    start: str | None
    end: str | None


@dataclass(frozen=True)
class WindowResult:
    label: str
    kind: str
    start: str | None
    end: str | None
    return_pct: float | None
    max_drawdown: float | None
    win_rate: float | None
    total_pnl: float | None
    trades: int
    spy_return: float | None
    qqq_return: float | None
    eq_return: float | None
    beats_spy: bool | None
    beats_qqq: bool | None


@dataclass(frozen=True)
class WalkForwardSummary:
    n_windows: int
    positive_windows: int
    negative_windows: int
    best_window: WindowResult | None
    worst_window: WindowResult | None
    avg_return: float | None
    avg_max_drawdown: float | None
    return_over_mdd: float | None
    worst_drawdown: float | None


@dataclass(frozen=True)
class WalkForwardValidation:
    full: WindowResult | None
    yearly: tuple[WindowResult, ...]
    rolling_6m: tuple[WindowResult, ...]
    rolling_12m: tuple[WindowResult, ...]
    summary: WalkForwardSummary
    data_start: str | None
    data_end: str | None
    bull_dependent: bool
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def generate_windows(data_min, data_max, *, yearly=True, roll6=True, roll12=True,
                     step_months=3) -> tuple[Window, ...]:
    """가용 [data_min,data_max]에서 full/yearly/rolling 윈도우를 만든다(순수)."""
    lo = pd.Timestamp(data_min)
    hi = pd.Timestamp(data_max)
    out: list[Window] = [Window(label="full", kind="full", start=None, end=None)]

    if yearly:
        for year in range(lo.year, hi.year + 1):
            y_start = max(lo, pd.Timestamp(year=year, month=1, day=1))
            y_end = min(hi, pd.Timestamp(year=year, month=12, day=31))
            if y_start <= y_end:
                out.append(Window(label=str(year), kind="year", start=_iso(y_start), end=_iso(y_end)))

    def _rolling(months: int, kind: str):
        cur = lo
        while True:
            w_end = cur + pd.DateOffset(months=months) - pd.DateOffset(days=1)
            if w_end > hi:
                break
            out.append(Window(label=f"{_iso(cur)}~{_iso(w_end)}", kind=kind,
                              start=_iso(cur), end=_iso(w_end)))
            cur = cur + pd.DateOffset(months=step_months)

    if roll6:
        _rolling(6, "roll6")
    if roll12:
        _rolling(12, "roll12")
    return tuple(out)


def _baseline_return(benchmark_cmp, name_prefix):
    for b in getattr(benchmark_cmp, "baselines", ()):
        if b.name.startswith(name_prefix):
            return b.cumulative_return
    return None


def make_window_result(label, kind, start, end, performance, benchmark_cmp) -> WindowResult:
    """sim performance + 벤치마크 비교로 WindowResult를 만든다(순수, 결측 안전)."""
    ret = None if performance is None else float(performance.cumulative_return)
    spy = _baseline_return(benchmark_cmp, "SPY")
    qqq = _baseline_return(benchmark_cmp, "QQQ")
    eq = _baseline_return(benchmark_cmp, "equal-weight")
    return WindowResult(
        label=label, kind=kind, start=start, end=end, return_pct=ret,
        max_drawdown=(None if performance is None else float(performance.max_drawdown)),
        win_rate=(None if performance is None else getattr(performance, "win_rate", None)),
        total_pnl=(None if performance is None else float(performance.total_pnl)),
        trades=(0 if performance is None else int(performance.num_trades)),
        spy_return=spy, qqq_return=qqq, eq_return=eq,
        beats_spy=(None if (ret is None or spy is None) else ret > spy),
        beats_qqq=(None if (ret is None or qqq is None) else ret > qqq),
    )


def compute_walk_forward_summary(results) -> WalkForwardSummary:
    """rolling 윈도우 집합의 워크포워드 집계."""
    rated = [r for r in results if r.return_pct is not None]
    positive = sum(1 for r in rated if r.return_pct > 0)
    negative = sum(1 for r in rated if r.return_pct < 0)
    best = max(rated, key=lambda r: r.return_pct) if rated else None
    worst = min(rated, key=lambda r: r.return_pct) if rated else None
    avg_ret = (sum(r.return_pct for r in rated) / len(rated)) if rated else None
    mdds = [r.max_drawdown for r in rated if r.max_drawdown is not None]
    avg_mdd = (sum(mdds) / len(mdds)) if mdds else None
    worst_dd = max(mdds) if mdds else None
    ret_over_mdd = (avg_ret / avg_mdd) if (avg_ret is not None and avg_mdd) else None
    return WalkForwardSummary(
        n_windows=len(rated), positive_windows=positive, negative_windows=negative,
        best_window=best, worst_window=worst, avg_return=avg_ret, avg_max_drawdown=avg_mdd,
        return_over_mdd=ret_over_mdd, worst_drawdown=worst_dd,
    )


def _years_covered(results) -> set[int]:
    years: set[int] = set()
    for r in results:
        for d in (r.start, r.end):
            if d:
                years.add(int(str(d)[:4]))
    return years


def build_walk_forward(full, yearly, roll6, roll12, summary, *, data_start, data_end):
    """워크포워드 검증을 종합한다(경고 + bull_dependent 판정)."""
    rolling = list(roll6) + list(roll12)
    warnings: list[str] = []

    # 실제로 매매가 일어난 연도만 regime 증거로 친다(warmup-only 0거래 연도는 제외).
    traded_years = {int(r.start[:4]) for r in yearly
                    if r.start and r.trades and r.return_pct is not None}
    if not traded_years:
        traded_years = _years_covered(list(yearly) + rolling)
    non_bull = traded_years - _BULL_YEARS
    bull_dependent = bool(traded_years) and not non_bull
    if bull_dependent:
        warnings.append(
            f"매매가 일어난 연도가 {sorted(traded_years)} (2025-2026 위주) — 강세장 밖 regime 검증 불가(데이터 한계)"
        )
    # 강세장 밖 연도가 날짜상 존재하지만 거래 표본이 없으면 별도 경고.
    empty_non_bull = sorted(int(r.start[:4]) for r in yearly
                            if r.start and int(r.start[:4]) not in _BULL_YEARS and not r.trades)
    if empty_non_bull:
        warnings.append(f"강세장 밖 연도 {empty_non_bull} 거래 표본 없음(warmup 구간) — out-of-bull 검증 불가")

    bad = [r for r in rolling if r.return_pct is not None and r.return_pct <= _BAD_WINDOW_RETURN]
    for r in bad:
        warnings.append(f"윈도우 {r.label} 수익률 {r.return_pct:.2%} — 큰 손실 구간")

    pos_pnls = [r.return_pct for r in rolling if r.return_pct is not None and r.return_pct > 0]
    if pos_pnls and max(pos_pnls) / sum(pos_pnls) > _REGIME_SHARE:
        warnings.append("rolling 수익이 한 윈도우에 집중 — 한 국면이 대부분 설명")

    if non_bull:
        # 비강세 연도 윈도우가 음수면 경고.
        non_bull_yearly = [r for r in yearly if r.start and int(r.start[:4]) in non_bull]
        losers = [r for r in non_bull_yearly if r.return_pct is not None and r.return_pct < 0]
        if losers:
            warnings.append(
                f"강세장 밖 연도 손실: {', '.join(f'{r.label}({r.return_pct:.2%})' for r in losers)}"
            )

    return WalkForwardValidation(
        full=full, yearly=tuple(yearly), rolling_6m=tuple(roll6), rolling_12m=tuple(roll12),
        summary=summary, data_start=data_start, data_end=data_end,
        bull_dependent=bull_dependent, warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _result_row(r: WindowResult) -> str:
    return (
        f"  {r.label:<22}{_fmt(r.return_pct):>9}{_fmt(r.max_drawdown):>9}"
        f"{_fmt(r.win_rate):>8}{(0 if r.total_pnl is None else r.total_pnl):>10.2f}{r.trades:>7}"
        f"{_fmt(r.spy_return):>9}{_fmt(r.qqq_return):>9}{_fmt(r.eq_return):>9}"
    )


def _table(title, rows) -> list[str]:
    lines = [title]
    lines.append(
        f"  {'window':<22}{'return':>9}{'mdd':>9}{'win':>8}{'pnl':>10}{'trd':>7}"
        f"{'SPY':>9}{'QQQ':>9}{'eqW':>9}"
    )
    for r in rows:
        lines.append(_result_row(r))
    return lines


def format_walk_forward(report: WalkForwardValidation) -> str:
    """사람이 읽는 워크포워드 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 110]
    lines.append("Long-Horizon / Walk-Forward Validation (측정 - 실주문 없음, next-bar-limit 3% 잠금)")
    lines.append("=" * 110)
    lines.append(f"data range: {report.data_start} ~ {report.data_end}")
    if report.full is not None:
        f = report.full
        lines.append(
            f"full-period: return {_fmt(f.return_pct)} MDD {_fmt(f.max_drawdown)} win {_fmt(f.win_rate)} "
            f"pnl {(0 if f.total_pnl is None else f.total_pnl):.2f} trades {f.trades}  "
            f"vs SPY {_fmt(f.spy_return)} / QQQ {_fmt(f.qqq_return)} / eqW {_fmt(f.eq_return)}"
        )

    lines.extend(_table("yearly:", report.yearly))
    lines.extend(_table("rolling 6-month:", report.rolling_6m))
    lines.extend(_table("rolling 12-month:", report.rolling_12m))

    s = report.summary
    lines.append("walk-forward summary (rolling windows):")
    lines.append(
        f"  windows {s.n_windows}  positive {s.positive_windows}  negative {s.negative_windows}  "
        f"avg return {_fmt(s.avg_return)}  avg MDD {_fmt(s.avg_max_drawdown)}  "
        f"ret/MDD {_fmt(s.return_over_mdd, '{:.2f}')}  worst DD {_fmt(s.worst_drawdown)}"
    )
    if s.best_window:
        lines.append(f"  best : {s.best_window.label} ({_fmt(s.best_window.return_pct)})")
    if s.worst_window:
        lines.append(f"  worst: {s.worst_window.label} ({_fmt(s.worst_window.return_pct)})")
    lines.append(f"  bull-market dependent: {report.bull_dependent}")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 110)
    return "\n".join(lines)
