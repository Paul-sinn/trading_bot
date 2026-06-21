"""선택적 승자 연장 What-if — 수익+건강한 60일 time_stop만 90/120일 연장했다면 어땠을지 본다(순수 측정).

trade_diag(트레이드 leg) + price_data(OHLC) + 벤치마크만 읽는다. 기본 전략/스캐너/디시전/사이징/
RiskGate/실 trade_log/포트폴리오를 바꾸지 않는다. 손실·불건강·미래없음 포지션은 연장하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/what-if 전용 — 동작 변경 없음(읽기만).

spec: specs/winner_extension_whatif.md
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

from algorithms.features import FeatureError, compute_features

_STOP_PCT = 0.15
_TRAIL_PCT = 0.20
_EXT_CAPS = (90, 120)
_MA_REGIME = 50     # SPY가 50MA 아래면 risk-off로 본다.


@dataclass(frozen=True)
class ExtensionCandidate:
    """수익 time_stop 1건의 연장 후보(건강하지 않으면 what-if는 None)."""

    symbol: str
    entry_date: str | None
    exit_date: str | None
    qty: float
    entry_price: float
    baseline_pnl: float
    healthy: bool
    reject_reasons: tuple[str, ...]
    pnl_90: float | None = None
    pnl_120: float | None = None
    reason_90: str | None = None
    reason_120: str | None = None
    added_dd_90: float | None = None
    added_dd_120: float | None = None
    incremental_90: float | None = None
    incremental_120: float | None = None


@dataclass(frozen=True)
class WinnerExtensionReport:
    """선택적 승자 연장 what-if 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    num_time_stop_exits: int
    profitable_count: int
    losing_count: int
    healthy_candidate_count: int
    rejected_count: int
    candidates: tuple[ExtensionCandidate, ...]
    rejected_reasons: tuple[tuple[str, int], ...]
    baseline_pnl_candidates: float | None
    whatif_pnl_90: float | None
    whatif_pnl_120: float | None
    incremental_90: float | None
    incremental_120: float | None
    top_benefit: tuple[tuple[str, float], ...]
    top_giveback: tuple[tuple[str, float], ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _date_str(ts) -> str:
    return str(ts.date()) if hasattr(ts, "date") else str(ts)


def _has_future(df, exit_ts) -> bool:
    if df is None or exit_ts not in df.index:
        return False
    pos = df.index.get_loc(exit_ts)
    return isinstance(pos, int) and pos + 1 < len(df.index)


def _spy_risk_off(benchmark, exit_ts) -> bool:
    """SPY 종가가 50MA 아래면 risk-off. 데이터 부족이면 판단 불가(False — 막지 않음)."""
    if benchmark is None:
        return False
    s = benchmark.loc[:exit_ts].dropna()
    if len(s) < _MA_REGIME:
        return False
    ma = s.rolling(_MA_REGIME).mean().iloc[-1]
    return bool(s.iloc[-1] < ma)


def _healthy_at_exit(df, exit_ts, benchmark):
    """청산일 건강 조건 점검 → (healthy, reasons). time_stop 청산이므로 trailing 미히트는 구조상 충족."""
    reasons: list[str] = []
    if not _has_future(df, exit_ts):
        reasons.append("no_future_data")
    try:
        snap = compute_features(
            df.loc[:exit_ts], benchmark=(benchmark.loc[:exit_ts] if benchmark is not None else None)
        )
    except FeatureError:
        return False, ("feature_error", *reasons)
    if snap.price_above_50ma is not True:
        reasons.append("price_below_50ma")
    if snap.price_above_20ma is not True:
        reasons.append("price_below_20ma")
    if benchmark is not None:
        if snap.relative_strength is None or snap.relative_strength <= 0:
            reasons.append("relative_strength<=0")
        if _spy_risk_off(benchmark, exit_ts):
            reasons.append("risk_off_regime")
    return (len(reasons) == 0), tuple(reasons)


def _extend_position(df, entry_ts, exit_ts, entry_price, qty, cap_days):
    """청산 이후부터 cap_days(entry 기준) 또는 stop/trailing 히트까지 보유했다면의 결과.

    returns (exit_price, exit_date, reason, added_drawdown) 또는 None(미래 바 없음).
    """
    idx = df.index
    if entry_ts not in idx or exit_ts not in idx:
        return None
    entry_pos = idx.get_loc(entry_ts)
    exit_pos = idx.get_loc(exit_ts)
    if not isinstance(entry_pos, int) or not isinstance(exit_pos, int):
        return None
    last = len(idx) - 1
    if exit_pos >= last:
        return None
    cap_pos = entry_pos + cap_days
    end_pos = min(cap_pos, last)

    stop_price = entry_price * (1.0 - _STOP_PCT)
    closes = df["close"]
    lows = df["low"] if "low" in df.columns else closes
    trailing_high = float(closes.iloc[entry_pos: exit_pos + 1].max())
    worst_dd = 0.0

    for pos in range(exit_pos + 1, end_pos + 1):
        close = float(closes.iloc[pos])
        low = float(lows.iloc[pos])
        trailing_high = max(trailing_high, close)
        if trailing_high > 0:
            worst_dd = max(worst_dd, (trailing_high - low) / trailing_high)
        if close <= stop_price:
            return close, _date_str(idx[pos]), "stop_loss_hit", worst_dd
        if close <= trailing_high * (1.0 - _TRAIL_PCT):
            return close, _date_str(idx[pos]), "trailing_stop_hit", worst_dd

    cap_close = float(closes.iloc[end_pos])
    reason = f"time_stop_{cap_days}" if end_pos == cap_pos else "data_end"
    return cap_close, _date_str(idx[end_pos]), reason, worst_dd


def compute_selective_winner_extension(
    trade_diag, price_data, *, benchmark_prices=None
) -> WinnerExtensionReport:
    """수익+건강한 time_stop 청산을 90/120일로 연장한 what-if을 산출한다(읽기 전용 — 입력 불변)."""
    time_stops = [l for l in trade_diag.trades if l.exit_reason == "time_stop"]
    profitable = [l for l in time_stops if l.pnl is not None and l.pnl > 0]
    losing = [l for l in time_stops if l.pnl is not None and l.pnl <= 0]

    candidates: list[ExtensionCandidate] = []
    reject_counter: Counter[str] = Counter()

    for leg in profitable:
        df = (price_data or {}).get(leg.symbol)
        base = dict(symbol=leg.symbol, entry_date=leg.entry_date, exit_date=leg.exit_date,
                    qty=leg.qty, entry_price=leg.entry_price, baseline_pnl=float(leg.pnl))
        if df is None:
            reject_counter["no_price_data"] += 1
            candidates.append(ExtensionCandidate(**base, healthy=False, reject_reasons=("no_price_data",)))
            continue

        exit_ts = pd.Timestamp(leg.exit_date)
        healthy, reasons = _healthy_at_exit(df, exit_ts, benchmark_prices)
        if not healthy:
            for r in reasons:
                reject_counter[r] += 1
            candidates.append(ExtensionCandidate(**base, healthy=False, reject_reasons=reasons))
            continue

        entry_ts = pd.Timestamp(leg.entry_date)
        ext = {cap: _extend_position(df, entry_ts, exit_ts, leg.entry_price, leg.qty, cap)
               for cap in _EXT_CAPS}

        def _pnl(cap):
            e = ext[cap]
            return ((e[0] - leg.entry_price) * leg.qty) if e is not None else None

        pnl_90, pnl_120 = _pnl(90), _pnl(120)
        candidates.append(ExtensionCandidate(
            **base, healthy=True, reject_reasons=(),
            pnl_90=pnl_90, pnl_120=pnl_120,
            reason_90=(ext[90][2] if ext[90] else None),
            reason_120=(ext[120][2] if ext[120] else None),
            added_dd_90=(ext[90][3] if ext[90] else None),
            added_dd_120=(ext[120][3] if ext[120] else None),
            incremental_90=(pnl_90 - leg.pnl) if pnl_90 is not None else None,
            incremental_120=(pnl_120 - leg.pnl) if pnl_120 is not None else None,
        ))

    healthy_cands = [c for c in candidates if c.healthy]
    rejected = [c for c in candidates if not c.healthy]

    def _sum(attr):
        vals = [getattr(c, attr) for c in healthy_cands if getattr(c, attr) is not None]
        return float(sum(vals)) if vals else None

    base_pnl = float(sum(c.baseline_pnl for c in healthy_cands)) if healthy_cands else None
    whatif_90, whatif_120 = _sum("pnl_90"), _sum("pnl_120")
    incr_90, incr_120 = _sum("incremental_90"), _sum("incremental_120")

    benefits = sorted(
        ((c.symbol, c.incremental_90) for c in healthy_cands if c.incremental_90 is not None),
        key=lambda kv: kv[1], reverse=True,
    )
    top_benefit = tuple(b for b in benefits if b[1] > 0)
    top_giveback = tuple(sorted((b for b in benefits if b[1] < 0), key=lambda kv: kv[1]))

    return WinnerExtensionReport(
        num_time_stop_exits=len(time_stops),
        profitable_count=len(profitable),
        losing_count=len(losing),
        healthy_candidate_count=len(healthy_cands),
        rejected_count=len(rejected),
        candidates=tuple(candidates),
        rejected_reasons=tuple(reject_counter.most_common()),
        baseline_pnl_candidates=base_pnl,
        whatif_pnl_90=whatif_90, whatif_pnl_120=whatif_120,
        incremental_90=incr_90, incremental_120=incr_120,
        top_benefit=top_benefit, top_giveback=top_giveback,
    )


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_selective_winner_extension(report: WinnerExtensionReport) -> str:
    """사람이 읽는 선택적 승자 연장 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 88]
    lines.append("Selective Winner Extension What-if (측정 - 실주문 없음, 실 trade_log 불변)")
    lines.append("=" * 88)
    lines.append(
        f"time_stop exits: {report.num_time_stop_exits}  profitable: {report.profitable_count}  "
        f"losing: {report.losing_count}  healthy candidates: {report.healthy_candidate_count}  "
        f"rejected: {report.rejected_count}"
    )
    lines.append(
        f"baseline PnL(candidates): {_fmt(report.baseline_pnl_candidates)}  "
        f"what-if 90d: {_fmt(report.whatif_pnl_90)} (Δ {_fmt(report.incremental_90, '{:+.2f}')})  "
        f"120d: {_fmt(report.whatif_pnl_120)} (Δ {_fmt(report.incremental_120, '{:+.2f}')})"
    )
    if report.rejected_reasons:
        lines.append("rejected reasons: " + ", ".join(f"{r}={n}" for r, n in report.rejected_reasons))

    healthy = [c for c in report.candidates if c.healthy]
    if healthy:
        lines.append(
            f"  {'symbol':<8}{'base_pnl':>10}{'pnl_90':>10}{'Δ90':>9}{'pnl_120':>10}{'Δ120':>9}{'r90':>16}"
        )
        for c in healthy[:40]:
            lines.append(
                f"  {c.symbol:<8}{_fmt(c.baseline_pnl):>10}{_fmt(c.pnl_90):>10}"
                f"{_fmt(c.incremental_90, '{:+.2f}'):>9}{_fmt(c.pnl_120):>10}"
                f"{_fmt(c.incremental_120, '{:+.2f}'):>9}{(c.reason_90 or '-'):>16}"
            )
    if report.top_benefit:
        lines.append("benefit most (90d): " + ", ".join(f"{s}({v:+.2f})" for s, v in report.top_benefit[:8]))
    if report.top_giveback:
        lines.append("give back (90d): " + ", ".join(f"{s}({v:+.2f})" for s, v in report.top_giveback[:8]))

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 88)
    return "\n".join(lines)
