"""결정 결과 채점 / 전진 검증 평가 — 로그 결정을 미래 가격으로 사후 채점한다(순수 측정).

forward 결과/재진입 재구성/집계/마크다운/JSONL은 순수 함수. 로그(JSONL)·시뮬 leg·로컬 OHLCV만 읽는다 —
스캐너/디시전/RiskGate/베이스라인을 바꾸지 않는다. forward만 사용(미래 누설 없음). 미래 바 부족은
unscorable로 표기(크래시 없음).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/decision_outcome.md
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field

HORIZONS = (1, 5, 10, 20, 60)
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_WIN_THRESHOLD = 0.10     # REJECT가 +10% 이상 → 놓친 승자. −10% 이하 → 옳은 거절.


@dataclass(frozen=True)
class ForwardOutcome:
    scorable: bool
    reason: str | None
    ref_price: float | None
    returns: dict           # horizon(int) -> float|None
    mfe: float | None
    mae: float | None
    stop_hit: bool | None
    trail_hit: bool | None
    time_close: bool | None
    forward_bars: int


@dataclass(frozen=True)
class ReentryContext:
    is_reentry: bool | None
    previous_exit_reason: str | None
    days_since_last_exit: int | None
    previous_exit_date: str | None
    same_symbol_reentry_count: int | None
    available: bool


@dataclass(frozen=True)
class ScoredRecord:
    date: str
    symbol: str
    decision: str
    reason: str
    outcome: ForwardOutcome
    reentry: ReentryContext

    def to_dict(self) -> dict:
        d = {"date": self.date, "symbol": self.symbol, "decision": self.decision, "reason": self.reason}
        o = asdict(self.outcome)
        o["returns"] = {str(k): v for k, v in self.outcome.returns.items()}
        d["outcome"] = o
        d["reentry"] = asdict(self.reentry)
        d["real_orders_placed"] = 0
        return d


def compute_forward_outcome(closes, highs, lows, *, horizons=HORIZONS, stop=_STOP, trail=_TRAIL,
                            max_hold=_MAX_HOLD) -> ForwardOutcome:
    """as_of(인덱스 0) 이후 forward 바로 결과를 계산한다. closes[0]=ref. 순수."""
    empty_returns = {h: None for h in horizons}
    if not closes:
        return ForwardOutcome(False, "as_of 가격 없음", None, empty_returns, None, None, None, None, None, 0)
    ref = closes[0]
    if ref is None or ref <= 0:
        return ForwardOutcome(False, "ref 가격 무효", None, empty_returns, None, None, None, None, None, 0)
    fc, fh, fl = closes[1:], highs[1:], lows[1:]
    n = len(fc)
    if n == 0:
        return ForwardOutcome(False, "forward 바 없음", float(ref), empty_returns, None, None, None, None, None, 0)

    returns = {h: (fc[h - 1] / ref - 1.0 if n >= h else None) for h in horizons}
    wh, wl = fh[:max_hold], fl[:max_hold]
    mfe = (max(wh) / ref - 1.0) if wh else None
    mae = (min(wl) / ref - 1.0) if wl else None

    stop_level = ref * (1.0 - stop)
    stop_hit = any(l <= stop_level for l in wl) if wl else None

    peak = ref
    trail_hit = False
    for hi, lo in zip(wh, wl):
        peak = max(peak, hi)
        if lo <= peak * (1.0 - trail):
            trail_hit = True
            break
    if not wl:
        trail_hit = None

    full_window = n >= max_hold
    if stop_hit or trail_hit:
        time_close = False
    elif full_window:
        time_close = True
    else:
        time_close = None       # 60바 미달 + 미발동 → 아직 미확정

    return ForwardOutcome(True, None, float(ref), returns, mfe, mae, stop_hit, trail_hit, time_close, n)


def compute_reentry_context(symbol, as_of_date, legs) -> ReentryContext:
    """같은 심볼의 이전 청산을 historical leg에서 찾아 재진입 컨텍스트를 재구성한다(report-only)."""
    if legs is None:
        return ReentryContext(None, None, None, None, None, available=False)
    sym_legs = [l for l in legs if l.symbol == symbol and l.entry_date]
    prior_entries = [l for l in sym_legs if str(l.entry_date) < str(as_of_date)]
    prior_exits = [l for l in sym_legs if l.exit_date and str(l.exit_date) < str(as_of_date)]
    if not prior_entries and not prior_exits:
        return ReentryContext(False, None, None, None, 0, available=True)
    last_exit = max(prior_exits, key=lambda l: str(l.exit_date)) if prior_exits else None
    days = None
    if last_exit is not None:
        days = _date_diff_days(last_exit.exit_date, as_of_date)
    return ReentryContext(
        is_reentry=True,
        previous_exit_reason=(last_exit.exit_reason if last_exit else None),
        days_since_last_exit=days,
        previous_exit_date=(str(last_exit.exit_date) if last_exit else None),
        same_symbol_reentry_count=len(prior_entries), available=True,
    )


def _date_diff_days(a, b):
    from datetime import date
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except ValueError:
        return None


def _outcome_from_df(df, as_of_date, *, max_hold=_MAX_HOLD, **kw) -> ForwardOutcome:
    """심볼 OHLC df + as_of 날짜로 forward 결과. df 없음/거래일 아님 → unscorable."""
    empty = {h: None for h in HORIZONS}
    if df is None:
        return ForwardOutcome(False, "심볼 데이터 없음", None, empty, None, None, None, None, None, 0)
    import pandas as pd
    try:
        ts = pd.Timestamp(as_of_date)
    except (ValueError, TypeError):
        return ForwardOutcome(False, "날짜 파싱 불가", None, empty, None, None, None, None, None, 0)
    idx = df.index
    if ts not in idx:
        return ForwardOutcome(False, "as_of 거래일 아님(데이터 밖)", None, empty, None, None, None, None, None, 0)
    loc = idx.get_loc(ts)
    if not isinstance(loc, int):
        return ForwardOutcome(False, "as_of 인덱스 모호", None, empty, None, None, None, None, None, 0)
    window = df.iloc[loc: loc + max_hold + 1]
    closes = [float(x) for x in window["close"].tolist()]
    highs = [float(x) for x in window["high"].tolist()]
    lows = [float(x) for x in window["low"].tolist()]
    return compute_forward_outcome(closes, highs, lows, max_hold=max_hold, **kw)


def score_records(records, price_data, legs, *, from_date=None, to_date=None) -> tuple:
    """로그 레코드(dict)들을 forward 결과 + 재진입 컨텍스트로 채점한다(순수, 입력 불변)."""
    out = []
    for rec in records:
        date = rec.get("date")
        symbol = rec.get("symbol")
        if date is None or symbol is None:
            continue
        if from_date and str(date) < str(from_date):
            continue
        if to_date and str(date) > str(to_date):
            continue
        decision = rec.get("decision", "SKIP")
        reason = rec.get("reason", "") or ""
        outcome = _outcome_from_df(price_data.get(symbol), date)
        reentry = compute_reentry_context(symbol, date, legs)
        out.append(ScoredRecord(date=str(date), symbol=str(symbol), decision=str(decision),
                                reason=str(reason), outcome=outcome, reentry=reentry))
    return tuple(out)


# ---- 집계 ----


@dataclass(frozen=True)
class BuySummary:
    n: int
    scorable: int
    avg_returns: dict
    median_returns: dict
    hit_rate: dict
    avg_mfe: float | None
    avg_mae: float | None
    stop_rate: float | None
    trail_rate: float | None
    time_close_rate: float | None
    top1_symbol: str | None
    top1_share: float | None
    top3_share: float | None


@dataclass(frozen=True)
class RejectSummary:
    n: int
    scorable: int
    missed_winners: tuple          # (symbol, date, ret60)
    good_rejects: tuple            # (symbol, date, ret60)
    common_reasons: tuple          # (reason, count)


@dataclass(frozen=True)
class OutcomeReport:
    buy: BuySummary
    reject: RejectSummary
    skip_count: int
    total: int
    unscorable: int
    reentry_available: bool
    in_sample: bool = False
    warnings: tuple = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _rate(flags):
    flags = [f for f in flags if f is not None]
    return (sum(1 for f in flags if f) / len(flags)) if flags else None


def summarize_buys(scored, *, horizons=HORIZONS) -> BuySummary:
    buys = [s for s in scored if s.decision == "BUY"]
    sc = [s for s in buys if s.outcome.scorable]
    avg = {h: _mean([s.outcome.returns.get(h) for s in sc]) for h in horizons}
    med = {h: _median([s.outcome.returns.get(h) for s in sc]) for h in horizons}
    hit = {}
    for h in horizons:
        vals = [s.outcome.returns.get(h) for s in sc if s.outcome.returns.get(h) is not None]
        hit[h] = (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
    # 60d 양수 수익 심볼 집중.
    pos = {}
    for s in sc:
        r = s.outcome.returns.get(60)
        if r is not None and r > 0:
            pos[s.symbol] = pos.get(s.symbol, 0.0) + r
    total = sum(pos.values())
    ranked = sorted(pos, key=lambda k: pos[k], reverse=True)
    top1 = ranked[0] if ranked else None
    top1_share = (pos[top1] / total) if (top1 and total > 0) else None
    top3_share = (sum(pos[k] for k in ranked[:3]) / total) if (ranked and total > 0) else None
    return BuySummary(
        n=len(buys), scorable=len(sc), avg_returns=avg, median_returns=med, hit_rate=hit,
        avg_mfe=_mean([s.outcome.mfe for s in sc]), avg_mae=_mean([s.outcome.mae for s in sc]),
        stop_rate=_rate([s.outcome.stop_hit for s in sc]),
        trail_rate=_rate([s.outcome.trail_hit for s in sc]),
        time_close_rate=_rate([s.outcome.time_close for s in sc]),
        top1_symbol=top1, top1_share=top1_share, top3_share=top3_share,
    )


def summarize_rejects(scored, *, threshold=_WIN_THRESHOLD) -> RejectSummary:
    rejects = [s for s in scored if s.decision == "REJECT"]
    sc = [s for s in rejects if s.outcome.scorable and s.outcome.returns.get(60) is not None]
    missed = tuple(sorted(((s.symbol, s.date, s.outcome.returns[60]) for s in sc
                           if s.outcome.returns[60] >= threshold), key=lambda t: t[2], reverse=True)[:10])
    good = tuple(sorted(((s.symbol, s.date, s.outcome.returns[60]) for s in sc
                         if s.outcome.returns[60] <= -threshold), key=lambda t: t[2])[:10])
    reasons: dict[str, int] = {}
    for s in rejects:
        key = s.reason.split("|")[0].strip() or "(none)"
        reasons[key] = reasons.get(key, 0) + 1
    common = tuple(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:8])
    return RejectSummary(n=len(rejects), scorable=len(sc), missed_winners=missed,
                         good_rejects=good, common_reasons=common)


def build_outcome_report(scored, *, in_sample=False) -> OutcomeReport:
    scored = tuple(scored)
    buy = summarize_buys(scored)
    reject = summarize_rejects(scored)
    skip_count = sum(1 for s in scored if s.decision == "SKIP")
    unscorable = sum(1 for s in scored if not s.outcome.scorable)
    reentry_available = any(s.reentry.available for s in scored)

    warnings: list[str] = []
    if in_sample:
        warnings.append("backfill = in-sample 재구성(historical sim 결정) — 진짜 라이브 전진 로그 아님. "
                        "전진 증거는 라이브 일별 로그 누적으로만 확보된다.")
    if buy.scorable == 0:
        warnings.append("채점 가능한 BUY 결정 없음 — 전진 증거 없음(인프라만)")
    elif buy.scorable < 20:
        warnings.append(f"채점 가능한 BUY 표본 적음(n={buy.scorable}) — 결론은 잠정")
    if buy.top1_share is not None and buy.top1_share > 0.35:
        warnings.append(f"BUY 60d 양수 수익이 {buy.top1_symbol}에 {buy.top1_share:.0%} 집중")
    warnings.append("단일 짧은 2025-2026 강세 구간 — out-of-bull 전진 증거 아님. 베이스라인 변경 없음.")
    return OutcomeReport(buy=buy, reject=reject, skip_count=skip_count, total=len(scored),
                         unscorable=unscorable, reentry_available=reentry_available,
                         in_sample=in_sample, warnings=tuple(warnings))


def scored_to_jsonl(scored) -> str:
    return "\n".join(json.dumps(s.to_dict(), ensure_ascii=False, sort_keys=True) for s in scored)


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_outcome_markdown(report: OutcomeReport) -> str:
    """사람이 읽는 결과 채점(측정 보조 — 매매 미사용). 질문에 답한다."""
    b, r = report.buy, report.reject
    lines: list[str] = []
    lines.append("# Decision Outcome Score / Forward Validation (측정 - 실주문 없음)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 로그/시뮬/로컬 OHLCV만 "
                 "읽어 사후 채점 — 스캐너/디시전/사이징/RiskGate·진입/청산/유니버스·베이스라인 미변경. forward만 사용.")
    lines.append("")
    lines.append(f"**요약**: 총 {report.total} 레코드 (BUY {b.n} / REJECT {r.n} / SKIP {report.skip_count}), "
                 f"unscorable {report.unscorable}, 재진입 컨텍스트 {'있음' if report.reentry_available else '없음'}.")
    lines.append("")

    lines.append("## BUY forward 결과")
    lines.append("")
    lines.append("| horizon | avg return | median | hit rate |")
    lines.append("|---|---|---|---|")
    for h in HORIZONS:
        lines.append(f"| {h}d | {_pct(b.avg_returns.get(h))} | {_pct(b.median_returns.get(h))} | "
                     f"{_pct(b.hit_rate.get(h), '{:.0%}')} |")
    lines.append("")
    lines.append(f"- avg MFE(60d) {_pct(b.avg_mfe)} · avg MAE(60d) {_pct(b.avg_mae)}")
    lines.append(f"- 시뮬 청산: stop {_pct(b.stop_rate, '{:.0%}')} · trailing {_pct(b.trail_rate, '{:.0%}')} · "
                 f"time-stop {_pct(b.time_close_rate, '{:.0%}')} (scorable BUY {b.scorable}/{b.n})")
    lines.append(f"- 60d 양수 수익 집중: top {b.top1_symbol or '-'} {_pct(b.top1_share, '{:.0%}')}, "
                 f"top3 {_pct(b.top3_share, '{:.0%}')}")
    lines.append("")

    lines.append("## REJECT 결과")
    lines.append("")
    lines.append(f"- 놓친 승자(60d ≥ +10%): " +
                 (", ".join(f"{s} {d} {_pct(v)}" for s, d, v in r.missed_winners) or "없음"))
    lines.append(f"- 옳은 거절(60d ≤ −10%): " +
                 (", ".join(f"{s} {d} {_pct(v)}" for s, d, v in r.good_rejects) or "없음"))
    lines.append(f"- 공통 거절 사유: " +
                 (", ".join(f"{reason}×{c}" for reason, c in r.common_reasons) or "없음"))
    lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    pos_buy = b.avg_returns.get(60)
    lines.append(f"- **BUY가 양의 forward return?** 60d 평균 {_pct(pos_buy)}, 5d {_pct(b.avg_returns.get(5))} "
                 f"→ {'양호' if (pos_buy or 0) > 0 else '비양호'} (scorable {b.scorable}).")
    lines.append(f"- **REJECT가 대체로 옳았나?** 옳은 거절 {len(r.good_rejects)} vs 놓친 승자 {len(r.missed_winners)} "
                 "(60d ±10% 기준).")
    lines.append(f"- **거절로 승자를 놓치나?** 놓친 승자 {len(r.missed_winners)}건 — 목록 참고.")
    best_h = max((h for h in HORIZONS if b.hit_rate.get(h) is not None),
                 key=lambda h: b.hit_rate.get(h), default=None)
    lines.append(f"- **BUY는 언제 더 통하나(5/10/20/60d)?** hit rate 최고 horizon = "
                 f"{(str(best_h) + 'd') if best_h else 'n/a'} ({_pct(b.hit_rate.get(best_h) if best_h else None, '{:.0%}')}).")
    lines.append(f"- **MU/ARM/top3 집중?** BUY 60d 양수 수익 top1 {b.top1_symbol or '-'} {_pct(b.top1_share, '{:.0%}')}, "
                 f"top3 {_pct(b.top3_share, '{:.0%}')}.")
    lines.append(f"- **unscorable 레코드 수?** {report.unscorable}/{report.total} (미래 바 부족).")
    enough = b.scorable >= 20
    if report.in_sample:
        evidence = "in-sample 측정뿐 — 라이브 전진 로그 아님(인프라+사후 측정)"
    elif enough:
        evidence = "잠정 측정 가능"
    else:
        evidence = "아직 인프라뿐(표본 부족)"
    lines.append(f"- **전진 증거 충분한가?** {evidence} — 단일 강세 구간이라 out-of-bull 검증은 불가.")
    lines.append("")

    if report.warnings:
        lines.append("## 경고")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")
    lines.append(f"`real_orders_placed = {report.real_orders_placed}`")
    lines.append("")
    return "\n".join(lines)
