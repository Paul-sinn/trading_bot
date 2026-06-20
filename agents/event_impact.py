"""이벤트 캘린더 영향 진단 — events.csv가 결과를 어떻게 바꾸는지 측정한다(순수 측정).

day report decisions(veto 사유) + 이벤트 provider만 읽는다. 상태/매매를 바꾸지 않는다. trade_diagnostics가
매매 레벨이라면, 이 모듈은 "이벤트가 어떤 진입을 막았나"를 본다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음.

spec: specs/event_impact.md
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

# policy의 이벤트 veto 사유("earnings/FOMC/CPI 등 고임팩트 이벤트 리스크 미확인")를 부분일치로 식별.
EVENT_VETO_SUBSTR = "이벤트 리스크 미확인"


@dataclass(frozen=True)
class BlockedCandidate:
    """이벤트로 차단된 후보 1건(날짜×심볼)."""

    symbol: str
    date: str
    raw_was_buy: bool
    would_have_been_buy: bool        # 이벤트가 유일한 veto 사유 + raw BUY
    event_type: str | None = None    # provider.events_on 조회(있으면)
    severity: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class EventImpactReport:
    """단일 런의 이벤트 차단 영향(측정 보조 — 판단 아님)."""

    blocked: tuple[BlockedCandidate, ...]
    num_blocked: int
    by_symbol: tuple[tuple[str, int], ...]
    by_event_type: tuple[tuple[str, int], ...]
    by_date: tuple[tuple[str, int], ...]
    would_be_buy_count: int
    top_event_veto_reasons: tuple[tuple[str, int], ...]
    symbols_affected: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class RunComparison:
    """assume-no-events vs events-csv 비교(측정 보조)."""

    bypass_trade_count: int
    events_trade_count: int
    trade_count_diff: int
    bypass_cumulative_return: float
    events_cumulative_return: float
    cumulative_return_diff: float
    bypass_max_drawdown: float
    events_max_drawdown: float
    max_drawdown_diff: float
    symbols_affected: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _event_details(event_provider, symbol: str, date: str):
    """provider에서 해당 날짜·심볼의 차단 이벤트 상세(첫 건)를 조회한다. 없으면 (None,None,None)."""
    if event_provider is None or not hasattr(event_provider, "events_on"):
        return None, None, None
    try:
        hits = event_provider.events_on(symbol, pd.Timestamp(date))
    except (ValueError, TypeError):
        return None, None, None
    if not hits:
        return None, None, None
    ev = hits[0]
    return ev.event_type, ev.severity, ev.notes


def compute_event_impact(multiday, *, event_provider=None) -> EventImpactReport:
    """multiday 결과에서 이벤트로 차단된 후보를 집계한다(읽기 전용)."""
    blocked: list[BlockedCandidate] = []
    reason_counter: Counter[str] = Counter()

    for dr in multiday.day_results:
        date = dr.report.report_date
        for d in dr.report.decisions:
            reasons = tuple(d.veto.reasons)
            event_reasons = [r for r in reasons if EVENT_VETO_SUBSTR in r]
            if not event_reasons:
                continue  # 이벤트로 차단된 게 아님(medium/low/통과 포함).

            for r in event_reasons:
                reason_counter[r] += 1

            other_reasons = [r for r in reasons if EVENT_VETO_SUBSTR not in r]
            raw_buy = getattr(d.raw_decision, "name", "") == "BUY"
            would_buy = raw_buy and not other_reasons   # 이벤트가 유일한 막힘 + raw BUY

            etype, severity, notes = _event_details(event_provider, d.symbol, date)
            blocked.append(BlockedCandidate(
                symbol=d.symbol, date=date, raw_was_buy=raw_buy,
                would_have_been_buy=would_buy,
                event_type=etype, severity=severity, notes=notes,
            ))

    by_symbol = Counter(b.symbol for b in blocked)
    by_event_type = Counter(b.event_type or "(unknown)" for b in blocked)
    by_date = Counter(b.date for b in blocked)

    return EventImpactReport(
        blocked=tuple(blocked),
        num_blocked=len(blocked),
        by_symbol=tuple(by_symbol.most_common()),
        by_event_type=tuple(by_event_type.most_common()),
        by_date=tuple(sorted(by_date.items())),
        would_be_buy_count=sum(1 for b in blocked if b.would_have_been_buy),
        top_event_veto_reasons=tuple(reason_counter.most_common()),
        symbols_affected=tuple(sorted({b.symbol for b in blocked})),
    )


def _buy_symbols(result) -> set[str]:
    """결과의 매수 진입 심볼 집합(trade_log의 buy)."""
    return {t.symbol for t in result.portfolio.trade_log if t.side == "buy"}


def compare_runs(bypass_result, events_result, *, event_provider=None) -> RunComparison:
    """assume-no-events 결과 vs events-csv 결과를 비교한다(읽기 전용)."""
    bp = bypass_result.performance
    ev = events_result.performance
    affected = sorted(_buy_symbols(bypass_result) ^ _buy_symbols(events_result))
    return RunComparison(
        bypass_trade_count=bp.num_trades,
        events_trade_count=ev.num_trades,
        trade_count_diff=ev.num_trades - bp.num_trades,
        bypass_cumulative_return=bp.cumulative_return,
        events_cumulative_return=ev.cumulative_return,
        cumulative_return_diff=ev.cumulative_return - bp.cumulative_return,
        bypass_max_drawdown=bp.max_drawdown,
        events_max_drawdown=ev.max_drawdown,
        max_drawdown_diff=ev.max_drawdown - bp.max_drawdown,
        symbols_affected=tuple(affected),
    )


def format_event_impact(report: EventImpactReport, *, max_rows: int = 50) -> str:
    """사람이 읽는 이벤트 영향 텍스트(측정 보조 — 판단 아님)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Event Impact Diagnostics (측정 - 실주문 없음)")
    lines.append("=" * 70)
    lines.append(f"candidates blocked by high events : {report.num_blocked}")
    lines.append(f"  would have been BUY (event-only) : {report.would_be_buy_count}")

    if report.by_symbol:
        lines.append("blocked by symbol:")
        for sym, n in report.by_symbol[:max_rows]:
            lines.append(f"  {sym:<10}{n:>5}")
    if report.by_event_type:
        lines.append("blocked by event_type:")
        for et, n in report.by_event_type[:max_rows]:
            lines.append(f"  {et:<12}{n:>5}")
    if report.by_date:
        lines.append("blocked by date:")
        for date, n in report.by_date[:max_rows]:
            lines.append(f"  {date}: {n}")
    if report.top_event_veto_reasons:
        lines.append("top event veto reasons:")
        for reason, n in report.top_event_veto_reasons[:10]:
            lines.append(f"  {n:>5}  {reason}")
    lines.append(f"symbols affected: {', '.join(report.symbols_affected) or '(none)'}")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)


def format_comparison(cmp: RunComparison) -> str:
    """assume-no-events vs events-csv 비교 텍스트."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Run Comparison: assume-no-events vs events-csv (측정 - 실주문 없음)")
    lines.append("=" * 70)
    lines.append(
        f"  trades       : bypass {cmp.bypass_trade_count} -> events {cmp.events_trade_count} "
        f"(diff {cmp.trade_count_diff:+d})"
    )
    lines.append(
        f"  cum_return   : bypass {cmp.bypass_cumulative_return:.2%} -> "
        f"events {cmp.events_cumulative_return:.2%} (diff {cmp.cumulative_return_diff:+.2%})"
    )
    lines.append(
        f"  max_drawdown : bypass {cmp.bypass_max_drawdown:.2%} -> "
        f"events {cmp.events_max_drawdown:.2%} (diff {cmp.max_drawdown_diff:+.2%})"
    )
    lines.append(f"  symbols affected: {', '.join(cmp.symbols_affected) or '(none)'}")
    lines.append(f"  real_orders_placed : {cmp.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
