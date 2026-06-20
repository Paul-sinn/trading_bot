"""시뮬 매매 레벨 진단 — multiday/historical 결과에서 매매 단위 분석을 산출한다(순수 측정).

상태를 바꾸지 않고 기존 산출물(trade_log, daily_snapshots, day report decisions)만 읽는다. perf_report가
포트폴리오 레벨 성과라면, 이 모듈은 매매 레벨(진입/청산/이유/증거)·drawdown 기간·veto 사유를 본다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/이벤트 캘린더 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

CRITICAL(가정 금지): TradeRecord에 날짜가 없어 일별 report_date + 누적 snapshot.trade_count로 매매
날짜를 복원한다. 미청산 포지션은 OPEN으로 두고, final_prices가 있을 때만 미실현 pnl을 계산한다(가짜 손익 금지).

spec: specs/trade_diagnostics.md
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class TradeLeg:
    """매매 1건(라운드트립 또는 미청산). exit_reason='OPEN'이면 미청산."""

    symbol: str
    entry_date: str | None
    exit_date: str | None
    entry_price: float
    exit_price: float | None
    qty: float
    pnl: float | None
    pnl_pct: float | None
    exit_reason: str | None
    entry_evidence: str | None = None


@dataclass(frozen=True)
class DrawdownPeriod:
    """최대 낙폭 구간. recovery_date는 트로프 이후 peak_equity 재달성 첫 날(없으면 None)."""

    peak_date: str | None
    peak_equity: float
    trough_date: str | None
    trough_equity: float
    max_drawdown: float
    recovery_date: str | None


@dataclass(frozen=True)
class TradeDiagnostics:
    """매매 레벨 진단 묶음(측정 보조 — 판단 아님)."""

    trades: tuple[TradeLeg, ...]
    best_trade: TradeLeg | None
    worst_trade: TradeLeg | None
    drawdown: DrawdownPeriod | None
    equity_over_time: tuple[tuple[str, float], ...]
    exposure_over_time: tuple[tuple[str, float], ...]
    top_symbols_by_pnl: tuple[tuple[str, float], ...]
    top_veto_reasons: tuple[tuple[str, int], ...]

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음."""
        return 0


def _trade_dates(day_results) -> list[str | None]:
    """일별 report_date + 누적 snapshot.trade_count로 각 매매(trade_log 인덱스)의 날짜를 복원한다."""
    out: list[str | None] = []
    prev = 0
    for dr in day_results:
        snap = dr.report.portfolio_snapshot
        if snap is None:
            continue  # 스냅샷 결측일은 매매 귀속 불가 — 건너뜀.
        count = snap.trade_count
        date = dr.report.report_date
        for _ in range(prev, count):
            out.append(date)
        prev = count
    return out


def _evidence_map(day_results) -> dict[tuple[str | None, str], str]:
    """(날짜, 심볼) → 진입 증거 스냅샷(tier/weight/account_loss/rationale)."""
    ev: dict[tuple[str | None, str], str] = {}
    for dr in day_results:
        date = dr.report.report_date
        for d in dr.report.decisions:
            try:
                snap = (
                    f"tier={d.tier} weight={d.position_weight:.3f} "
                    f"account_loss={d.account_loss_pct:.3f} :: {d.rationale}"
                )
            except (AttributeError, TypeError, ValueError):
                snap = getattr(d, "rationale", "")
            ev[(date, d.symbol)] = snap
    return ev


def _pair_trades(trade_log, dates, final_prices, evidence) -> list[TradeLeg]:
    """매수→매도 FIFO 매칭으로 TradeLeg를 만든다(분수주 지원). 잔여는 OPEN."""
    open_lots: dict[str, list[list]] = {}
    legs: list[TradeLeg] = []

    for i, tr in enumerate(trade_log):
        date = dates[i] if i < len(dates) else None
        if tr.side == "buy":
            ev = evidence.get((date, tr.symbol))
            open_lots.setdefault(tr.symbol, []).append([date, tr.price, tr.shares, ev])
        elif tr.side == "sell":
            remaining = tr.shares
            lots = open_lots.get(tr.symbol, [])
            while remaining > _EPS and lots:
                lot = lots[0]
                matched = min(lot[2], remaining)
                entry_price = lot[1]
                pnl = (tr.price - entry_price) * matched
                pnl_pct = (tr.price - entry_price) / entry_price if entry_price else None
                legs.append(TradeLeg(
                    symbol=tr.symbol, entry_date=lot[0], exit_date=date,
                    entry_price=entry_price, exit_price=tr.price, qty=matched,
                    pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason=tr.exit_reason or "sell", entry_evidence=lot[3],
                ))
                lot[2] -= matched
                remaining -= matched
                if lot[2] <= _EPS:
                    lots.pop(0)

    # 잔여 미청산 lot → OPEN leg.
    for symbol, lots in open_lots.items():
        for lot in lots:
            if lot[2] <= _EPS:
                continue
            entry_price = lot[1]
            price = (final_prices or {}).get(symbol)
            if price is not None and entry_price:
                pnl = (price - entry_price) * lot[2]
                pnl_pct = (price - entry_price) / entry_price
            else:
                pnl = None
                pnl_pct = None
            legs.append(TradeLeg(
                symbol=symbol, entry_date=lot[0], exit_date=None,
                entry_price=entry_price, exit_price=price, qty=lot[2],
                pnl=pnl, pnl_pct=pnl_pct, exit_reason="OPEN", entry_evidence=lot[3],
            ))
    return legs


def _drawdown_period(equity_curve: list[tuple[str, float]]) -> DrawdownPeriod | None:
    """equity 곡선에서 최대 낙폭 구간(peak/trough/recovery)을 찾는다."""
    if not equity_curve:
        return None

    cur_peak = float("-inf")
    cur_peak_date: str | None = None
    mdd = 0.0
    best: tuple[str | None, float, str, float] | None = None

    for date, eq in equity_curve:
        if eq > cur_peak:
            cur_peak = eq
            cur_peak_date = date
        if cur_peak > 0:
            dd = (cur_peak - eq) / cur_peak
            if dd > mdd:
                mdd = dd
                best = (cur_peak_date, cur_peak, date, eq)

    if best is None:
        return None
    peak_date, peak_eq, trough_date, trough_eq = best

    recovery_date: str | None = None
    seen_trough = False
    for date, eq in equity_curve:
        if date == trough_date:
            seen_trough = True
            continue
        if seen_trough and eq >= peak_eq:
            recovery_date = date
            break

    return DrawdownPeriod(
        peak_date=peak_date, peak_equity=peak_eq,
        trough_date=trough_date, trough_equity=trough_eq,
        max_drawdown=mdd, recovery_date=recovery_date,
    )


def compute_trade_diagnostics(multiday, *, final_prices=None) -> TradeDiagnostics:
    """multiday(또는 HistoricalResult.multiday) 결과에서 매매 레벨 진단을 산출한다(읽기 전용)."""
    day_results = multiday.day_results
    trade_log = multiday.portfolio.trade_log

    dates = _trade_dates(day_results)
    evidence = _evidence_map(day_results)
    trades = _pair_trades(trade_log, dates, final_prices, evidence)

    equity_over_time: list[tuple[str, float]] = []
    exposure_over_time: list[tuple[str, float]] = []
    for dr in day_results:
        snap = dr.report.portfolio_snapshot
        if snap is None:
            continue
        equity_over_time.append((dr.report.report_date, snap.equity))
        exposure_over_time.append((dr.report.report_date, snap.total_exposure))

    priced = [t for t in trades if t.pnl is not None]
    best = max(priced, key=lambda t: t.pnl) if priced else None
    worst = min(priced, key=lambda t: t.pnl) if priced else None

    pnl_by_symbol: Counter[str] = Counter()
    for t in priced:
        pnl_by_symbol[t.symbol] += t.pnl
    top_symbols = tuple(sorted(pnl_by_symbol.items(), key=lambda kv: kv[1], reverse=True))

    veto_counter: Counter[str] = Counter()
    for dr in day_results:
        for d in dr.report.decisions:
            if not d.veto.passed:
                for reason in d.veto.reasons:
                    veto_counter[reason] += 1

    return TradeDiagnostics(
        trades=tuple(trades),
        best_trade=best,
        worst_trade=worst,
        drawdown=_drawdown_period(equity_over_time),
        equity_over_time=tuple(equity_over_time),
        exposure_over_time=tuple(exposure_over_time),
        top_symbols_by_pnl=top_symbols,
        top_veto_reasons=tuple(veto_counter.most_common()),
    )


def _fmt_pnl(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def format_trade_diagnostics(diag: TradeDiagnostics, *, max_rows: int = 50) -> str:
    """사람이 읽는 매매 진단 텍스트(측정 보조 — 판단 아님)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Trade-Level Diagnostics (측정 - 실주문 없음)")
    lines.append("=" * 70)

    lines.append(f"trades: {len(diag.trades)}")
    lines.append(
        f"  {'symbol':<8}{'entry':<12}{'exit':<12}{'entryP':>10}{'exitP':>10}"
        f"{'qty':>10}{'pnl':>10}{'pnl%':>9}  reason"
    )
    for t in diag.trades[:max_rows]:
        lines.append(
            f"  {t.symbol:<8}{(t.entry_date or '-'):<12}{(t.exit_date or '-'):<12}"
            f"{t.entry_price:>10.2f}"
            f"{(f'{t.exit_price:.2f}' if t.exit_price is not None else '-'):>10}"
            f"{t.qty:>10.4f}{_fmt_pnl(t.pnl):>10}{_fmt_pct(t.pnl_pct):>9}  {t.exit_reason}"
        )
    if len(diag.trades) > max_rows:
        lines.append(f"  ... (+{len(diag.trades) - max_rows} more)")

    if diag.best_trade is not None:
        b = diag.best_trade
        lines.append(f"best  : {b.symbol} pnl={_fmt_pnl(b.pnl)} ({_fmt_pct(b.pnl_pct)})")
    if diag.worst_trade is not None:
        w = diag.worst_trade
        lines.append(f"worst : {w.symbol} pnl={_fmt_pnl(w.pnl)} ({_fmt_pct(w.pnl_pct)})")

    dd = diag.drawdown
    if dd is not None:
        lines.append(
            f"max drawdown: {dd.max_drawdown:.2%}  peak={dd.peak_date}({dd.peak_equity:.2f}) "
            f"trough={dd.trough_date}({dd.trough_equity:.2f}) "
            f"recovery={dd.recovery_date or '(none)'}"
        )

    if diag.top_symbols_by_pnl:
        lines.append("top symbols by pnl:")
        for sym, pnl in diag.top_symbols_by_pnl[:10]:
            lines.append(f"  {sym:<8}{pnl:>12.2f}")

    if diag.top_veto_reasons:
        lines.append("top veto reasons:")
        for reason, n in diag.top_veto_reasons[:10]:
            lines.append(f"  {n:>5}  {reason}")

    lines.append("exposure over time (date: exposure):")
    for date, val in diag.exposure_over_time[:max_rows]:
        lines.append(f"  {date}: {val:.2f}")
    if len(diag.exposure_over_time) > max_rows:
        lines.append(f"  ... (+{len(diag.exposure_over_time) - max_rows} more)")

    if diag.best_trade is not None and diag.best_trade.entry_evidence:
        lines.append(f"entry evidence (best): {diag.best_trade.entry_evidence}")

    lines.append(f"real_orders_placed : {diag.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
