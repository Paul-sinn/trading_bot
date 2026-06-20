"""한정매수 체결 What-if — order_plan의 limit_buy_shadow가 일봉 OHLC로 체결됐을지 추정한다(순수 측정).

OrderPlanReport + price_data(OHLC)만 읽는다. 실제 시뮬 체결/포트폴리오/매매/veto를 바꾸지 않는다 —
어떤 결과도 실 트레이드에 적용하지 않는다. 진입일 단일 바만 평가(cancel_end_of_day 존중, 다음날 추격 없음).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/what-if 전용 — 동작 변경 없음(읽기만).

spec: specs/limit_fill_whatif.md
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

_MISSED_PROFIT_WARN = 0.2     # 수익의 20%+가 미체결로 누락되면 경고.
_TIGHT_FILL_RATE = 0.8        # 체결률 < 0.8 → 상한이 빡빡.
_LOOSE_FILL_RATE = 0.98       # 체결률 ≥ 0.98 → 상한이 느슨.


@dataclass(frozen=True)
class FillRow:
    """한 계획의 체결 추정 1건."""

    symbol: str
    entry_date: str | None
    reference_price: float | None
    limit_price: float
    status: str                  # filled / missed / unknown
    shadow_fill_price: float | None
    fill_at: str | None          # open / limit / None
    pnl: float | None            # 실 트레이드 PnL(trade_diag 조인, 있으면)


@dataclass(frozen=True)
class LimitFillReport:
    """한정매수 체결 what-if 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    rows: tuple[FillRow, ...]
    total_planned: int
    filled_count: int
    missed_count: int
    unknown_count: int
    fill_rate: float | None
    avg_limit_distance: float | None
    missed_by_symbol: tuple[tuple[str, int], ...]
    best_missed: FillRow | None
    worst_missed: FillRow | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _bar(price_data, symbol, date):
    """진입일 OHLC (open, high, low). 심볼/날짜 결측이면 None(unknown)."""
    df = (price_data or {}).get(symbol)
    if df is None or date is None or not {"open", "high", "low"}.issubset(getattr(df, "columns", [])):
        return None
    try:
        ts = pd.Timestamp(date)
    except (ValueError, TypeError):
        return None
    if ts not in df.index:
        return None
    row = df.loc[ts]
    try:
        return float(row["open"]), float(row["high"]), float(row["low"])
    except (KeyError, TypeError, ValueError):
        return None


def _classify(o: float, l: float, limit: float):
    """매수 한정가 일봉 체결: open≤limit→open, 아니면 low≤limit→limit, 그 외 missed."""
    if o <= limit:
        return "filled", o, "open"
    if l <= limit:
        return "filled", limit, "limit"
    return "missed", None, None


def _pnl_map(trade_diag):
    """(symbol, entry_date) → 실 PnL 합(trade_diag.trades). 없으면 빈 dict."""
    out: dict[tuple, float] = {}
    if trade_diag is None:
        return out
    for t in getattr(trade_diag, "trades", ()):  # noqa: B007
        if t.pnl is None:
            continue
        key = (t.symbol, t.entry_date)
        out[key] = out.get(key, 0.0) + t.pnl
    return out


def compute_limit_fill_whatif(order_plan_report, price_data, *, trade_diag=None) -> LimitFillReport:
    """주문계획의 한정매수 체결 여부를 일봉 OHLC로 추정한다(읽기 전용 — 입력 불변)."""
    pnl_map = _pnl_map(trade_diag)

    rows: list[FillRow] = []
    for p in order_plan_report.plans:
        if p.suggested_limit_price is None:
            continue  # 한정매수 주문이 아님(no_trade/ref 결측) — 평가 대상 아님.
        limit = float(p.suggested_limit_price)
        bar = _bar(price_data, p.symbol, p.entry_date)
        if bar is None:
            status, fill_px, fill_at = "unknown", None, None
        else:
            o, _h, l = bar
            status, fill_px, fill_at = _classify(o, l, limit)
        rows.append(FillRow(
            symbol=p.symbol, entry_date=p.entry_date, reference_price=p.reference_price,
            limit_price=limit, status=status, shadow_fill_price=fill_px, fill_at=fill_at,
            pnl=pnl_map.get((p.symbol, p.entry_date)),
        ))

    filled = [r for r in rows if r.status == "filled"]
    missed = [r for r in rows if r.status == "missed"]
    unknown = [r for r in rows if r.status == "unknown"]
    known = len(filled) + len(missed)
    fill_rate = (len(filled) / known) if known > 0 else None

    distances = [
        (r.limit_price / r.reference_price - 1.0)
        for r in rows if r.reference_price and r.reference_price > 0
    ]
    avg_distance = (sum(distances) / len(distances)) if distances else None

    missed_by_symbol = Counter(r.symbol for r in missed)
    missed_priced = [r for r in missed if r.pnl is not None]
    best_missed = max(missed_priced, key=lambda r: r.pnl) if missed_priced else None
    worst_missed = min(missed_priced, key=lambda r: r.pnl) if missed_priced else None

    warnings: list[str] = []
    total_profit = sum(r.pnl for r in rows if r.pnl is not None and r.pnl > 0)
    missed_profit = sum(r.pnl for r in missed if r.pnl is not None and r.pnl > 0)
    if total_profit > 0 and missed_profit / total_profit >= _MISSED_PROFIT_WARN:
        warnings.append(
            f"수익 트레이드의 {missed_profit / total_profit:.0%}가 미체결로 누락 "
            f"(한정매수 상한이 일부 진입을 놓침)"
        )
    if fill_rate is not None:
        if fill_rate < _TIGHT_FILL_RATE:
            warnings.append(
                f"상한이 빡빡(체결률 {fill_rate:.0%}) — slippage 상한을 높이면 누락 감소"
            )
        elif fill_rate >= _LOOSE_FILL_RATE:
            warnings.append(
                f"상한이 느슨(체결률 {fill_rate:.0%}, 거의 전량 진입일 체결) — 더 타이트해도 무방"
            )

    return LimitFillReport(
        rows=tuple(rows),
        total_planned=len(rows),
        filled_count=len(filled),
        missed_count=len(missed),
        unknown_count=len(unknown),
        fill_rate=fill_rate,
        avg_limit_distance=avg_distance,
        missed_by_symbol=tuple(missed_by_symbol.most_common()),
        best_missed=best_missed,
        worst_missed=worst_missed,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_limit_fill_whatif(report: LimitFillReport, *, max_rows: int = 60) -> str:
    """사람이 읽는 한정매수 체결 what-if 텍스트(측정 보조 — 실행 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 84)
    lines.append("Limit Order Fill What-if (측정 - 실주문 없음, 실 체결 불변)")
    lines.append("=" * 84)
    lines.append(
        f"planned {report.total_planned}  filled {report.filled_count}  "
        f"missed {report.missed_count}  unknown {report.unknown_count}  "
        f"fill_rate {_fmt(report.fill_rate, '{:.0%}')}  "
        f"avg_limit_dist {_fmt(report.avg_limit_distance, '{:.2%}')}"
    )
    lines.append(f"  {'symbol':<8}{'entry_date':<12}{'limit_px':>10}{'status':>9}{'fill_px':>10}{'at':>7}{'pnl':>10}")
    for r in report.rows[:max_rows]:
        lines.append(
            f"  {r.symbol:<8}{(r.entry_date or '-'):<12}{_fmt(r.limit_price):>10}{r.status:>9}"
            f"{_fmt(r.shadow_fill_price):>10}{(r.fill_at or '-'):>7}{_fmt(r.pnl):>10}"
        )
    if len(report.rows) > max_rows:
        lines.append(f"  ... (+{len(report.rows) - max_rows} more)")

    if report.missed_by_symbol:
        top = ", ".join(f"{s}({n})" for s, n in report.missed_by_symbol[:8])
        lines.append(f"missed by symbol: {top}")
    if report.best_missed is not None:
        lines.append(
            f"best missed : {report.best_missed.symbol} pnl={_fmt(report.best_missed.pnl)}"
        )
    if report.worst_missed is not None:
        lines.append(
            f"worst missed: {report.worst_missed.symbol} pnl={_fmt(report.worst_missed.pnl)}"
        )
    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 84)
    return "\n".join(lines)
