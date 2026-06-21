"""후보 청산 정책 워크포워드 검증 — 후보 vs 잠긴 베이스라인을 롤링 윈도우로 본다(순수 측정).

윈도우 생성·비교·안정성 판정·마크다운은 순수 함수. 윈도우별 재시뮬은 러너가 run_sim 청산 플래그만
바꿔 만든다. 진입/유니버스/스캐너/디시전/사이징/RiskGate 미변경. **베이스라인 승격 없음.**

매매가 일어난 윈도우가 모두 2025-2026이면 OUT_OF_BULL_VALIDATION = NOT_AVAILABLE로 정직하게 표기한다
(가짜 결론 금지).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/exit_walk_forward.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from agents.walk_forward import Window   # (label, kind, start, end) 재사용

_BULL_YEARS = frozenset({2025, 2026})
_CONCENTRATION = 0.6     # 단일 윈도우 우위가 양수 우위 합의 60% 초과면 집중.


@dataclass(frozen=True)
class PolicyWindow:
    label: str
    kind: str
    start: str | None
    end: str | None
    policy: str
    result: object        # ExitVariantResult
    eq_return: float | None


@dataclass(frozen=True)
class WindowCompare:
    label: str
    kind: str
    start: str | None
    end: str | None
    base_return: float | None
    cand_return: float | None
    base_pnl: float | None
    cand_pnl: float | None
    base_mdd: float | None
    cand_mdd: float | None
    base_ratio: float | None
    cand_ratio: float | None
    base_trades: int
    cand_trades: int
    cand_beats_pnl: bool
    cand_beats_ratio: bool
    cand_worse_mdd: bool
    pnl_advantage: float | None


@dataclass(frozen=True)
class StabilityVerdict:
    n_windows: int
    cand_beats_pnl: int
    cand_beats_ratio: int
    cand_worse_mdd: int
    cand_negative: int
    base_negative: int
    worst_window: WindowCompare | None
    best_window: WindowCompare | None
    advantage_concentrated: bool
    top_advantage_share: float | None


@dataclass(frozen=True)
class ExitWalkForwardReport:
    policy_windows: tuple[PolicyWindow, ...]
    compares: tuple[WindowCompare, ...]
    verdict: StabilityVerdict
    out_of_bull: str          # "NOT_AVAILABLE" | "AVAILABLE"
    out_of_bull_reason: str | None
    data_start: str | None
    data_end: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _iso(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _rolling(lo, hi, months, step, kind, out):
    cur = lo
    while True:
        w_end = cur + pd.DateOffset(months=months) - pd.DateOffset(days=1)
        if w_end > hi:
            break
        out.append(Window(label=f"{_iso(cur)}~{_iso(w_end)}", kind=kind, start=_iso(cur), end=_iso(w_end)))
        cur = cur + pd.DateOffset(months=step)


def generate_exit_windows(data_min, data_max) -> tuple[Window, ...]:
    """가용 범위에서 year/quarter/roll3/roll6/roll12 윈도우(rolling step 3m)."""
    lo, hi = pd.Timestamp(data_min), pd.Timestamp(data_max)
    out: list[Window] = []
    for year in range(lo.year, hi.year + 1):
        ys = max(lo, pd.Timestamp(year=year, month=1, day=1))
        ye = min(hi, pd.Timestamp(year=year, month=12, day=31))
        if ys <= ye:
            out.append(Window(label=str(year), kind="year", start=_iso(ys), end=_iso(ye)))
    for year in range(lo.year, hi.year + 1):
        for q in range(4):
            qs = pd.Timestamp(year=year, month=q * 3 + 1, day=1)
            qe = qs + pd.DateOffset(months=3) - pd.DateOffset(days=1)
            s, e = max(lo, qs), min(hi, qe)
            if s <= e and qs <= hi and qe >= lo:
                out.append(Window(label=f"{year}-Q{q + 1}", kind="quarter", start=_iso(s), end=_iso(e)))
    _rolling(lo, hi, 3, 3, "roll3", out)
    _rolling(lo, hi, 6, 3, "roll6", out)
    _rolling(lo, hi, 12, 3, "roll12", out)
    return tuple(out)


def compute_window_compares(base_windows, cand_windows) -> tuple[WindowCompare, ...]:
    """라벨로 baseline/candidate PolicyWindow를 짝지어 윈도우 비교를 만든다."""
    cand_by = {(w.kind, w.label): w for w in cand_windows}
    out = []
    for bw in base_windows:
        cw = cand_by.get((bw.kind, bw.label))
        if cw is None:
            continue
        br, cr = bw.result, cw.result
        adv = (None if (cr.total_pnl is None or br.total_pnl is None)
               else cr.total_pnl - br.total_pnl)
        out.append(WindowCompare(
            label=bw.label, kind=bw.kind, start=bw.start, end=bw.end,
            base_return=br.cumulative_return, cand_return=cr.cumulative_return,
            base_pnl=br.total_pnl, cand_pnl=cr.total_pnl, base_mdd=br.max_drawdown, cand_mdd=cr.max_drawdown,
            base_ratio=br.return_over_mdd, cand_ratio=cr.return_over_mdd,
            base_trades=br.trades, cand_trades=cr.trades,
            cand_beats_pnl=(adv is not None and adv > 0),
            cand_beats_ratio=(cr.return_over_mdd is not None and br.return_over_mdd is not None
                              and cr.return_over_mdd > br.return_over_mdd),
            cand_worse_mdd=(cr.max_drawdown is not None and br.max_drawdown is not None
                            and cr.max_drawdown > br.max_drawdown),
            pnl_advantage=adv,
        ))
    return tuple(out)


def _year_of(s):
    try:
        return int(str(s)[:4])
    except (TypeError, ValueError):
        return None


def compute_stability_verdict(compares, *, bull_years=_BULL_YEARS) -> StabilityVerdict:
    """후보 vs baseline 윈도우 비교를 집계한다(활성 윈도우=양쪽 trades>0)."""
    active = [c for c in compares if c.base_trades > 0 or c.cand_trades > 0]
    rated = [c for c in active if c.cand_return is not None]
    beats_pnl = sum(1 for c in active if c.cand_beats_pnl)
    beats_ratio = sum(1 for c in active if c.cand_beats_ratio)
    worse_mdd = sum(1 for c in active if c.cand_worse_mdd)
    cand_neg = sum(1 for c in rated if c.cand_return < 0)
    base_neg = sum(1 for c in rated if c.base_return is not None and c.base_return < 0)
    worst = min(rated, key=lambda c: c.cand_return) if rated else None
    best = max(rated, key=lambda c: c.cand_return) if rated else None

    advs = [c.pnl_advantage for c in active if c.pnl_advantage is not None and c.pnl_advantage > 0]
    adv_total = sum(advs)
    top_share = (max(advs) / adv_total) if adv_total > 0 else None
    concentrated = top_share is not None and top_share > _CONCENTRATION

    return StabilityVerdict(
        n_windows=len(active), cand_beats_pnl=beats_pnl, cand_beats_ratio=beats_ratio,
        cand_worse_mdd=worse_mdd, cand_negative=cand_neg, base_negative=base_neg,
        worst_window=worst, best_window=best, advantage_concentrated=concentrated,
        top_advantage_share=top_share,
    )


def _traded_years(policy_windows):
    """실제 매매가 일어난 연도. 캘린더 연도(year 윈도우) 우선 — 롤링 윈도우 start년은 warmup으로
    실제 매매 연도와 어긋날 수 있어 신뢰하지 않는다(없으면 매매 윈도우 날짜로 폴백)."""
    yearly = [w for w in policy_windows if w.kind == "year" and getattr(w.result, "trades", 0)]
    if yearly:
        return {y for w in yearly if (y := _year_of(w.label)) is not None}
    years = set()
    for w in policy_windows:
        if getattr(w.result, "trades", 0):
            for d in (w.start, w.end):
                if (y := _year_of(d)) is not None:
                    years.add(y)
    return years


def build_exit_walk_forward(policy_windows, compares, verdict, *, data_start, data_end) -> ExitWalkForwardReport:
    """워크포워드 검증을 종합한다(out-of-bull 마킹 + 경고). 승격 없음."""
    traded = _traded_years(policy_windows)
    non_bull = traded - _BULL_YEARS
    if non_bull:
        out_of_bull, reason = "AVAILABLE", None
    else:
        out_of_bull = "NOT_AVAILABLE"
        reason = (f"insufficient local data history — 매매 윈도우가 모두 {sorted(traded)} "
                  "(2025-2026 강세장). 강세장 밖 검증 불가.")

    warnings: list[str] = []
    warnings.append(f"OUT_OF_BULL_VALIDATION = {out_of_bull}" + (f" ({reason})" if reason else ""))
    if verdict.advantage_concentrated:
        warnings.append(
            f"후보 PnL 우위가 한 윈도우에 집중(최대 단일 윈도우가 양수 우위의 {verdict.top_advantage_share:.0%}) — 일관성 약함"
        )
    warnings.append("측정 단계 — 단일 짧은 강세 구간. 베이스라인 승격 없음(no promotion yet).")

    return ExitWalkForwardReport(
        policy_windows=tuple(policy_windows), compares=tuple(compares), verdict=verdict,
        out_of_bull=out_of_bull, out_of_bull_reason=reason,
        data_start=data_start, data_end=data_end, warnings=tuple(warnings),
    )


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _num(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _pw_row(w: PolicyWindow) -> str:
    r = w.result
    return (
        f"| {w.kind} | {w.label} | {w.policy} | {_pct(r.cumulative_return)} | {_num(r.total_pnl)} | "
        f"{_pct(r.max_drawdown)} | {_num(r.return_over_mdd)} | {_pct(r.win_rate, '{:.0%}')} | {r.trades} | "
        f"{_num(r.avg_trade_pnl)} | {r.top1_symbol or '-'} {_pct(r.top1_share, '{:.0%}')} | "
        f"{_pct(r.top3_share, '{:.0%}')} | {r.best_symbol or '-'}/{r.worst_symbol or '-'} | "
        f"{r.beats_spy} | {r.beats_qqq} | {_pct(w.eq_return)} |"
    )


def format_exit_walk_forward_markdown(report: ExitWalkForwardReport) -> str:
    """마크다운 리포트(reports/exit_candidate_walk_forward.md). 측정 보조 — 매매 미사용."""
    v = report.verdict
    lines: list[str] = []
    lines.append("# Exit Candidate Walk-Forward Validation (측정 - 실주문 없음, 진입 next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 진입 모델·유니버스·"
                 "스캐너/디시전/사이징/RiskGate·베이스라인 미변경. **이 단계에서 베이스라인 승격 없음.**")
    lines.append("")
    lines.append(f"**데이터 범위**: {report.data_start} ~ {report.data_end}  ·  "
                 f"**OUT_OF_BULL_VALIDATION = {report.out_of_bull}**"
                 + (f" — {report.out_of_bull_reason}" if report.out_of_bull_reason else ""))
    lines.append("")
    lines.append("> **방법론 주의**: 각 윈도우는 start_date만 바꾼 독립 재시뮬(자본 리셋, 지표는 이전 "
                 "히스토리 사용). 윈도우 끝 미청산 포지션은 마지막 종가로 마크(미실현)되므로, 청산 타이밍이 "
                 "다른 정책 간 **윈도우 raw PnL 비교는 노이즈가 크다** — 신뢰 가능한 비교 지표는 "
                 "cumulative return과 return/MDD다.")
    lines.append("")

    # 안정성 판정.
    lines.append("## 안정성 판정 (후보 vs locked_baseline, 활성 윈도우)")
    lines.append("")
    lines.append(f"- 활성 윈도우: {v.n_windows}")
    lines.append(f"- 후보가 PnL로 이긴 윈도우: **{v.cand_beats_pnl}/{v.n_windows}**")
    lines.append(f"- 후보가 return/MDD로 이긴 윈도우: **{v.cand_beats_ratio}/{v.n_windows}**")
    lines.append(f"- 후보 MDD가 더 나쁜 윈도우: {v.cand_worse_mdd}/{v.n_windows}")
    lines.append(f"- 음수 윈도우: 후보 {v.cand_negative}, baseline {v.base_negative}")
    if v.best_window:
        lines.append(f"- best 후보 윈도우: {v.best_window.kind}/{v.best_window.label} "
                     f"({_pct(v.best_window.cand_return)})")
    if v.worst_window:
        lines.append(f"- worst 후보 윈도우: {v.worst_window.kind}/{v.worst_window.label} "
                     f"({_pct(v.worst_window.cand_return)})")
    lines.append(f"- 우위 집중도: 최대 단일 윈도우 = 양수 우위의 {_pct(v.top_advantage_share, '{:.0%}')} "
                 f"({'집중' if v.advantage_concentrated else '분산'})")
    lines.append("")

    # 윈도우×정책 표.
    lines.append("## 윈도우 × 정책 결과")
    lines.append("")
    lines.append("| kind | window | policy | return | PnL | MDD | ret/MDD | win | trades | avg PnL "
                 "| top1 | top3 | best/worst | >SPY | >QQQ | eqW |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for w in report.policy_windows:
        lines.append(_pw_row(w))
    lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    lines.append(f"- **후보가 윈도우 전반에서 일관되게 baseline을 이기나?** return/MDD(신뢰 지표) "
                 f"{v.cand_beats_ratio}/{v.n_windows}, PnL(노이즈) {v.cand_beats_pnl}/{v.n_windows} — "
                 f"{'대체로 우위' if v.cand_beats_ratio * 2 >= v.n_windows else '일관성 약함(절반 미만)'}.")
    lines.append(f"- **한 분기 때문에만 이기나?** 최대 단일 윈도우가 양수 우위의 {_pct(v.top_advantage_share, '{:.0%}')} "
                 f"→ {'한 윈도우 집중(주의)' if v.advantage_concentrated else '한 윈도우 집중 아님'}.")
    lines.append(f"- **drawdown을 너무 자주 키우나?** 후보 MDD가 더 나쁜 윈도우 {v.cand_worse_mdd}/{v.n_windows}.")
    lines.append("- **ARM/top3 집중 리스크가 남아 있나?** 직전 단계(exit_candidate_validation)에서 후보 ARM 제거 "
                 "−29%로 쏠림 확인 — 워크포워드는 이를 해소하지 못함(여전히 집중 리스크).")
    lines.append(f"- **승격할 증거가 충분한가?** **아니오.** OUT_OF_BULL_VALIDATION = {report.out_of_bull}; "
                 "강세장 밖/장기 검증 불가 + 집중 리스크 잔존.")
    lines.append("- **잠긴 베이스라인을 그대로 둬야 하나?** **예.** 승격 없음.")
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
