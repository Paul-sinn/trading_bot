"""진입 실행 슬리피지 + 갭 스트레스 진단 — 3% limit vs next-open을 실행비용/갭리스크 하에서 비교(순수 측정).

두 실행(3% limit / next-open)의 트레이드 결과에 슬리피지/갭가드를 사후 적용한다(재시뮬 없음, 원 결과 불변).
라이브/기본 전략/스캐너/디시전/사이징/RiskGate를 바꾸지 않는다. 라우팅 PnL은 심볼별 PnL 합 근사.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/진단 전용 — 동작 변경 없음(읽기만).

spec: specs/execution_stress.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

SLIPPAGES = (0.0, 0.001, 0.0025, 0.005, 0.01)   # 0 / 0.10% / 0.25% / 0.50% / 1.00%
GAP_GUARDS = (0.03, 0.05, 0.08)
_GUARD_SLIPPAGE = 0.0025                          # 갭 가드 행의 대표 슬리피지(0.25%).
_LIMIT = "next-bar-limit-3%"
_NEXT_OPEN = "next-open"


@dataclass(frozen=True)
class StressResult:
    """한 (정책 × 슬리피지 × 갭가드) 조합의 메트릭(what-if 근사)."""

    policy: str
    slippage_pct: float
    gap_guard: float | None
    cumulative_return: float
    max_drawdown_proxy: float | None
    win_rate: float | None
    total_pnl: float
    trades: int
    avg_holding_days: float | None
    return_mdd_ratio: float | None
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    skipped_gap_entries: int
    skipped_profitable_pnl: float

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class StressReport:
    """실행 스트레스 비교 묶음. real_orders_placed는 항상 0."""

    results: tuple[StressResult, ...]
    best_by_return_mdd: StressResult | None
    starting_cash: float
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _base_legs(diag):
    """diag.trades → 경량 dict 리스트(원본 불변)."""
    out = []
    for l in diag.trades:
        if l.pnl is None:
            continue
        out.append({
            "symbol": l.symbol, "entry_date": l.entry_date, "exit_date": l.exit_date,
            "pnl": float(l.pnl), "qty": float(l.qty), "entry_price": float(l.entry_price),
        })
    return out


def _entry_gap(df, entry_date) -> float | None:
    """진입 바 갭(open/직전 종가 − 1). 결측이면 None."""
    if df is None or not {"open", "close"}.issubset(getattr(df, "columns", [])):
        return None
    try:
        ts = pd.Timestamp(entry_date)
    except (ValueError, TypeError):
        return None
    if ts not in df.index:
        return None
    pos = df.index.get_loc(ts)
    if not isinstance(pos, int) or pos < 1:
        return None
    prev_close = float(df["close"].iloc[pos - 1])
    op = float(df["open"].iloc[pos])
    return (op / prev_close - 1.0) if prev_close > 0 else None


def _adjusted_pnl(leg, slippage) -> float:
    """슬리피지(진입가 가산) 적용 PnL = pnl − entry_price×slip×qty."""
    return leg["pnl"] - leg["entry_price"] * slippage * leg["qty"]


def _mdd_proxy(items) -> float | None:
    """청산일순 누적손익 낙폭(달러). items=(exit_date, pnl)."""
    dated = [(d, p) for d, p in items if d is not None]
    if not dated:
        return None
    dated.sort(key=lambda kv: kv[0])
    cum = peak = mdd = 0.0
    for _d, p in dated:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def _last_date(*diags) -> str | None:
    dates = [l.exit_date for d in diags for l in d.trades if l.exit_date is not None]
    return max(dates) if dates else None


def _holding(leg, last_date) -> int | None:
    try:
        entry = pd.Timestamp(leg["entry_date"])
        end = pd.Timestamp(leg["exit_date"]) if leg["exit_date"] else (pd.Timestamp(last_date) if last_date else None)
    except (ValueError, TypeError):
        return None
    return int((end - entry).days) if end is not None else None


def _metrics(policy, slippage, guard, kept, skipped, starting_cash, last_date) -> StressResult:
    total = float(sum(_adjusted_pnl(l, slippage) for l in kept))
    closed = [(l["exit_date"], _adjusted_pnl(l, slippage)) for l in kept if l["exit_date"] is not None]
    wins = sum(1 for _d, p in closed if p > 0)
    win_rate = (wins / len(closed)) if closed else None
    mdd = _mdd_proxy([(l["exit_date"], _adjusted_pnl(l, slippage)) for l in kept])
    holds = [h for h in (_holding(l, last_date) for l in kept) if h is not None]
    cum = total / starting_cash if starting_cash > 0 else 0.0
    ratio = (cum / (mdd / starting_cash)) if (mdd and mdd > 0 and starting_cash > 0) else None

    pnl_by: dict[str, float] = {}
    for l in kept:
        pnl_by[l["symbol"]] = pnl_by.get(l["symbol"], 0.0) + _adjusted_pnl(l, slippage)
    pos_total = sum(v for v in pnl_by.values() if v > 0)
    top = max(pnl_by, key=lambda s: pnl_by[s]) if pnl_by else None
    share = (pnl_by[top] / pos_total) if (top and pos_total > 0 and pnl_by[top] > 0) else None

    skipped_profit = float(sum(_adjusted_pnl(l, slippage) for l in skipped
                               if _adjusted_pnl(l, slippage) > 0))

    return StressResult(
        policy=policy, slippage_pct=slippage, gap_guard=guard,
        cumulative_return=cum, max_drawdown_proxy=mdd, win_rate=win_rate, total_pnl=total,
        trades=len(kept), avg_holding_days=(statistics.fmean(holds) if holds else None),
        return_mdd_ratio=ratio, top_symbol=top, top_symbol_pnl_share=share,
        skipped_gap_entries=len(skipped), skipped_profitable_pnl=skipped_profit,
    )


def compute_execution_stress(
    limit3_diag, next_open_diag, price_data, *, starting_cash: float = 1000.0,
) -> StressReport:
    """3% limit vs next-open을 슬리피지/갭가드 하에서 비교한다(읽기 전용 — 입력 불변)."""
    price_data = price_data or {}
    last_date = _last_date(limit3_diag, next_open_diag)
    limit_legs = _base_legs(limit3_diag)
    nopen_legs = _base_legs(next_open_diag)

    results: list[StressResult] = []
    # limit3 × 슬리피지(가드 없음).
    for slip in SLIPPAGES:
        results.append(_metrics(_LIMIT, slip, None, limit_legs, [], starting_cash, last_date))
    # next-open × 슬리피지(가드 없음).
    for slip in SLIPPAGES:
        results.append(_metrics(_NEXT_OPEN, slip, None, nopen_legs, [], starting_cash, last_date))
    # next-open × 갭가드(대표 슬리피지).
    for guard in GAP_GUARDS:
        kept, skipped = [], []
        for leg in nopen_legs:
            gap = _entry_gap(price_data.get(leg["symbol"]), leg["entry_date"])
            (skipped if (gap is not None and gap > guard) else kept).append(leg)
        results.append(_metrics(_NEXT_OPEN, _GUARD_SLIPPAGE, guard, kept, skipped, starting_cash, last_date))

    rated = [r for r in results if r.return_mdd_ratio is not None]
    best = max(rated, key=lambda r: r.return_mdd_ratio) if rated else None

    warnings: list[str] = []
    # 한 심볼 집중: 무비용 next-open에서 top share가 큰 경우.
    base_no = next((r for r in results if r.policy == _NEXT_OPEN and r.slippage_pct == 0.0
                    and r.gap_guard is None), None)
    if base_no is not None and base_no.top_symbol_pnl_share is not None and base_no.top_symbol_pnl_share >= 0.5:
        warnings.append(
            f"next-open 수익이 {base_no.top_symbol}에 집중({base_no.top_symbol_pnl_share:.0%}) — 분산 약함"
        )
    return StressReport(results=tuple(results), best_by_return_mdd=best,
                        starting_cash=starting_cash, warnings=tuple(warnings))


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_execution_stress(report: StressReport) -> str:
    """실행 스트레스 비교표(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 104]
    lines.append("Entry Execution Stress (측정 - 실주문 없음, 슬리피지/갭 사후 적용 — 원 결과 불변)")
    lines.append("=" * 104)
    lines.append(
        f"  {'policy':<18}{'slip':>7}{'guard':>7}{'cum_ret':>9}{'MDDx':>9}{'win':>7}{'total_PnL':>12}"
        f"{'trades':>7}{'ret/MDD':>8}{'topShare':>9}{'skip':>6}{'skipPnL':>9}"
    )
    for r in report.results:
        lines.append(
            f"  {r.policy:<18}{r.slippage_pct:>6.2%}{(_fmt(r.gap_guard, '{:.0%}') if r.gap_guard else '-'):>7}"
            f"{_fmt(r.cumulative_return):>9}{_fmt(r.max_drawdown_proxy, '{:.2f}'):>9}{_fmt(r.win_rate):>7}"
            f"{r.total_pnl:>12.2f}{r.trades:>7}{_fmt(r.return_mdd_ratio, '{:.2f}'):>8}"
            f"{_fmt(r.top_symbol_pnl_share, '{:.0%}'):>9}{r.skipped_gap_entries:>6}{r.skipped_profitable_pnl:>9.2f}"
        )
    if report.best_by_return_mdd is not None:
        b = report.best_by_return_mdd
        lines.append(
            f"best return/MDD : {b.policy} slip {b.slippage_pct:.2%} guard "
            f"{(_fmt(b.gap_guard, '{:.0%}') if b.gap_guard else '-')} "
            f"(ret/MDD {_fmt(b.return_mdd_ratio, '{:.2f}')}, cum {_fmt(b.cumulative_return)})"
        )
    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 104)
    return "\n".join(lines)
