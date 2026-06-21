"""다음 바 한정매수 체결 What-if — 같은-바 lookahead를 제거하고 다음 거래 바로 체결을 추정한다(순수 측정).

시그널/참조는 entry_date지만, 한정매수는 **다음 거래 바**에 제출돼 체결됐을지 평가한다(참조 종가는 그 바가
끝나기 전엔 몰랐으므로 같은-바 체결은 과대평가 가능). OrderPlanReport + price_data(OHLC)만 읽는다. 실제
시뮬 체결/포트폴리오/매매/veto를 바꾸지 않는다 — 어떤 결과도 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/what-if 전용 — 동작 변경 없음(읽기만).

spec: specs/next_bar_fill_whatif.md
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

# 같은-바 모델 단일 진실 재사용(동일 패키지 내부 헬퍼).
from agents.limit_fill_whatif import _bar, _classify, _pnl_map

_SAME_BAR_LOOSE = 0.98     # 같은-바 체결률이 이 이상인데 다음-바가 크게 낮으면 "오해 소지" 경고.
_MATERIAL_GAP = 0.10       # |same − next| 체결률 차이가 10%p+면 진입 가능성 크게 달라짐.


@dataclass(frozen=True)
class NextBarFillRow:
    """한 계획의 같은-바/다음-바 체결 추정 1건."""

    symbol: str
    entry_date: str | None
    next_bar_date: str | None
    reference_price: float | None
    limit_price: float
    same_bar_status: str            # filled / missed / unknown
    next_bar_status: str            # filled / missed / unknown
    next_shadow_fill_price: float | None
    next_fill_at: str | None        # open / limit / None
    gap: float | None               # next_open / reference_price − 1
    pnl: float | None


@dataclass(frozen=True)
class NextBarFillReport:
    """다음-바 체결 what-if 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    rows: tuple[NextBarFillRow, ...]
    same_bar_fill_rate: float | None
    next_bar_fill_rate: float | None
    next_filled_count: int
    next_missed_count: int
    next_unknown_count: int
    missed_profitable_count: int
    missed_profitable_pnl: float
    avg_next_bar_gap: float | None
    missed_by_symbol: tuple[tuple[str, int], ...]
    best_missed: NextBarFillRow | None
    worst_missed: NextBarFillRow | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _next_bar(price_data, symbol, date):
    """entry_date 다음 거래 바 (next_date_str, open, high, low). 결측/마지막 바면 None."""
    df = (price_data or {}).get(symbol)
    if df is None or date is None or not {"open", "high", "low"}.issubset(getattr(df, "columns", [])):
        return None
    try:
        ts = pd.Timestamp(date)
    except (ValueError, TypeError):
        return None
    if ts not in df.index:
        return None
    pos = df.index.get_loc(ts)
    if not isinstance(pos, int):       # 중복 날짜 등 — 안전하게 unknown.
        return None
    nxt = pos + 1
    if nxt >= len(df.index):           # 다음 바 없음(마지막 바였음).
        return None
    nrow = df.iloc[nxt]
    ndate = df.index[nxt]
    try:
        date_str = str(ndate.date()) if hasattr(ndate, "date") else str(ndate)
        return date_str, float(nrow["open"]), float(nrow["high"]), float(nrow["low"])
    except (KeyError, TypeError, ValueError):
        return None


def _fill_rate(rows, attr: str) -> float | None:
    """attr 상태에서 filled / (filled+missed). 알려진 게 없으면 None."""
    filled = sum(1 for r in rows if getattr(r, attr) == "filled")
    missed = sum(1 for r in rows if getattr(r, attr) == "missed")
    known = filled + missed
    return (filled / known) if known > 0 else None


def compute_next_bar_fill_whatif(order_plan_report, price_data, *, trade_diag=None) -> NextBarFillReport:
    """한정매수 계획을 다음 거래 바로 체결 추정한다(같은-바도 대조 계산, 읽기 전용 — 입력 불변)."""
    pnl_map = _pnl_map(trade_diag)

    rows: list[NextBarFillRow] = []
    for p in order_plan_report.plans:
        if p.suggested_limit_price is None:
            continue
        limit = float(p.suggested_limit_price)

        # 같은-바(대조용).
        same = _bar(price_data, p.symbol, p.entry_date)
        if same is None:
            same_status = "unknown"
        else:
            so, _sh, sl = same
            same_status, _sp, _sa = _classify(so, sl, limit)

        # 다음-바.
        nxt = _next_bar(price_data, p.symbol, p.entry_date)
        if nxt is None:
            next_date, next_status, next_px, next_at, gap = None, "unknown", None, None, None
        else:
            next_date, no, nh, nl = nxt
            next_status, next_px, next_at = _classify(no, nl, limit)
            gap = (no / p.reference_price - 1.0) if (p.reference_price and p.reference_price > 0) else None

        rows.append(NextBarFillRow(
            symbol=p.symbol, entry_date=p.entry_date, next_bar_date=next_date,
            reference_price=p.reference_price, limit_price=limit,
            same_bar_status=same_status, next_bar_status=next_status,
            next_shadow_fill_price=next_px, next_fill_at=next_at, gap=gap,
            pnl=pnl_map.get((p.symbol, p.entry_date)),
        ))

    same_rate = _fill_rate(rows, "same_bar_status")
    next_rate = _fill_rate(rows, "next_bar_status")
    next_filled = sum(1 for r in rows if r.next_bar_status == "filled")
    next_missed = [r for r in rows if r.next_bar_status == "missed"]
    next_unknown = sum(1 for r in rows if r.next_bar_status == "unknown")

    missed_profitable = [r for r in next_missed if r.pnl is not None and r.pnl > 0]
    missed_profit_pnl = sum(r.pnl for r in missed_profitable)

    gaps = [r.gap for r in rows if r.gap is not None]
    avg_gap = (sum(gaps) / len(gaps)) if gaps else None

    missed_priced = [r for r in next_missed if r.pnl is not None]
    best_missed = max(missed_priced, key=lambda r: r.pnl) if missed_priced else None
    worst_missed = min(missed_priced, key=lambda r: r.pnl) if missed_priced else None

    warnings: list[str] = []
    if same_rate is not None and next_rate is not None:
        if same_rate >= _SAME_BAR_LOOSE and (same_rate - next_rate) >= _MATERIAL_GAP:
            warnings.append(
                f"같은-바 체결률({same_rate:.0%})이 오해 소지로 높음(lookahead) — "
                f"다음-바에선 {next_rate:.0%}로 떨어짐"
            )
        if abs(same_rate - next_rate) >= _MATERIAL_GAP:
            warnings.append(
                f"다음-바 모델이 진입 가능성을 크게 바꿈(같은-바 {same_rate:.0%} vs 다음-바 {next_rate:.0%})"
            )
    if missed_profitable:
        warnings.append(
            f"다음-바 모델에서 수익 트레이드 {len(missed_profitable)}건 미체결 "
            f"(누락 PnL {missed_profit_pnl:.2f})"
        )

    return NextBarFillReport(
        rows=tuple(rows),
        same_bar_fill_rate=same_rate,
        next_bar_fill_rate=next_rate,
        next_filled_count=next_filled,
        next_missed_count=len(next_missed),
        next_unknown_count=next_unknown,
        missed_profitable_count=len(missed_profitable),
        missed_profitable_pnl=float(missed_profit_pnl),
        avg_next_bar_gap=avg_gap,
        missed_by_symbol=tuple(Counter(r.symbol for r in next_missed).most_common()),
        best_missed=best_missed,
        worst_missed=worst_missed,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_next_bar_fill_whatif(report: NextBarFillReport, *, max_rows: int = 60) -> str:
    """사람이 읽는 다음-바 체결 what-if 텍스트(측정 보조 — 실행 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("Next-Bar Limit Fill What-if (측정 - 실주문 없음, lookahead 제거, 실 체결 불변)")
    lines.append("=" * 90)
    lines.append(
        f"same-bar fill_rate {_fmt(report.same_bar_fill_rate, '{:.0%}')}  vs  "
        f"next-bar fill_rate {_fmt(report.next_bar_fill_rate, '{:.0%}')}"
    )
    lines.append(
        f"next: filled {report.next_filled_count}  missed {report.next_missed_count}  "
        f"unknown {report.next_unknown_count}  avg_next_gap {_fmt(report.avg_next_bar_gap, '{:.2%}')}  "
        f"missed_profitable {report.missed_profitable_count} (pnl {report.missed_profitable_pnl:.2f})"
    )
    lines.append(
        f"  {'symbol':<8}{'entry':<12}{'next_bar':<12}{'limit':>9}{'same':>9}{'next':>9}"
        f"{'next_fill':>10}{'gap':>8}{'pnl':>9}"
    )
    for r in report.rows[:max_rows]:
        lines.append(
            f"  {r.symbol:<8}{(r.entry_date or '-'):<12}{(r.next_bar_date or '-'):<12}"
            f"{_fmt(r.limit_price):>9}{r.same_bar_status:>9}{r.next_bar_status:>9}"
            f"{_fmt(r.next_shadow_fill_price):>10}{_fmt(r.gap, '{:+.2%}'):>8}{_fmt(r.pnl):>9}"
        )
    if len(report.rows) > max_rows:
        lines.append(f"  ... (+{len(report.rows) - max_rows} more)")

    if report.missed_by_symbol:
        top = ", ".join(f"{s}({n})" for s, n in report.missed_by_symbol[:8])
        lines.append(f"missed by symbol (next-bar): {top}")
    if report.best_missed is not None:
        lines.append(f"best missed : {report.best_missed.symbol} pnl={_fmt(report.best_missed.pnl)}")
    if report.worst_missed is not None:
        lines.append(f"worst missed: {report.worst_missed.symbol} pnl={_fmt(report.worst_missed.pnl)}")
    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 90)
    return "\n".join(lines)
