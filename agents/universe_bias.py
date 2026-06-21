"""유니버스 확장/편향 테스트 — 잠긴 베이스라인을 유니버스만 바꿔 비교한다(순수 측정).

요약·집중도·마크다운 포맷은 순수 함수. 유니버스별 재시뮬은 러너(experiments/universe_bias_test.py)가
run_sim으로 한다. 상태/매매/veto/전략/기본 유니버스를 바꾸지 않는다. 갭 가드·winner extension 미적용,
next-open 미사용. 레버리지 ETF 미혼합.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/universe_bias.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

_TOP1_CONCENTRATION = 0.35
_TOP3_CONCENTRATION = 0.65


@dataclass(frozen=True)
class UniverseResult:
    name: str
    requested: tuple[str, ...]
    present: tuple[str, ...]
    missing: tuple[str, ...]
    zero_trade: tuple[str, ...]
    cumulative_return: float | None
    total_pnl: float | None
    max_drawdown: float | None
    return_over_mdd: float | None
    win_rate: float | None
    trades: int
    top1_symbol: str | None
    top1_share: float | None
    top3_symbols: tuple[str, ...]
    top3_share: float | None
    best_symbol: str | None
    worst_symbol: str | None
    quarterly: tuple[tuple[str, float], ...]
    spy_return: float | None
    qqq_return: float | None
    eq_return: float | None
    beats_spy: bool | None
    beats_qqq: bool | None


@dataclass(frozen=True)
class UniverseBiasReport:
    variants: tuple[UniverseResult, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def compute_top_shares(symbol_perf):
    """symbol_perf(.symbol/.total_pnl)로 top1/top3 양수 PnL share + best/worst."""
    if not symbol_perf:
        return None, None, (), None, None, None
    ranked = sorted(symbol_perf, key=lambda p: p.total_pnl, reverse=True)
    best = ranked[0].symbol
    worst = ranked[-1].symbol
    pos = [p for p in ranked if p.total_pnl > 0]
    pos_total = sum(p.total_pnl for p in pos)
    if pos_total <= 0:
        return best, None, (), None, best, worst
    top1 = pos[0].symbol
    top1_share = pos[0].total_pnl / pos_total
    top3 = tuple(p.symbol for p in pos[:3])
    top3_share = sum(p.total_pnl for p in pos[:3]) / pos_total
    return top1, top1_share, top3, top3_share, best, worst


def _baseline_return(benchmark_cmp, name_prefix):
    for b in getattr(benchmark_cmp, "baselines", ()):
        if b.name.startswith(name_prefix):
            return b.cumulative_return
    return None


def summarize_universe(name, requested, present, performance, robustness, benchmark_cmp) -> UniverseResult:
    """한 유니버스 변형의 결과를 요약한다(순수, 결측 안전)."""
    requested = tuple(requested)
    present = tuple(present)
    missing = tuple(s for s in requested if s not in set(present))

    symbol_perf = tuple(getattr(robustness, "symbol_perf", ()))
    traded_syms = {p.symbol for p in symbol_perf}
    zero_trade = tuple(s for s in present if s not in traded_syms)

    top1, top1_share, top3, top3_share, best, worst = compute_top_shares(symbol_perf)
    quarterly = tuple((w.label, w.pnl) for w in getattr(robustness, "windows", ()))

    ret = None if performance is None else float(performance.cumulative_return)
    mdd = None if performance is None else float(performance.max_drawdown)
    spy = _baseline_return(benchmark_cmp, "SPY")
    qqq = _baseline_return(benchmark_cmp, "QQQ")
    eq = _baseline_return(benchmark_cmp, "equal-weight")

    return UniverseResult(
        name=name, requested=requested, present=present, missing=missing, zero_trade=zero_trade,
        cumulative_return=ret, total_pnl=(None if performance is None else float(performance.total_pnl)),
        max_drawdown=mdd, return_over_mdd=(ret / mdd if (ret is not None and mdd) else None),
        win_rate=(None if performance is None else getattr(performance, "win_rate", None)),
        trades=(0 if performance is None else int(performance.num_trades)),
        top1_symbol=top1, top1_share=top1_share, top3_symbols=top3, top3_share=top3_share,
        best_symbol=best, worst_symbol=worst, quarterly=quarterly,
        spy_return=spy, qqq_return=qqq, eq_return=eq,
        beats_spy=(None if (ret is None or spy is None) else ret > spy),
        beats_qqq=(None if (ret is None or qqq is None) else ret > qqq),
    )


def _get(variants, name):
    return next((v for v in variants if v.name == name), None)


def build_universe_bias(variants) -> UniverseBiasReport:
    """변형들을 묶고 편향/집중 경고를 만든다."""
    variants = tuple(variants)
    warnings: list[str] = []

    base = _get(variants, "baseline")
    expanded = _get(variants, "expanded")
    no_mu = _get(variants, "expanded_no_mu")

    if base and base.top1_share is not None and base.top1_share > _TOP1_CONCENTRATION:
        warnings.append(
            f"baseline top 심볼 {base.top1_symbol}이 양수 PnL의 {base.top1_share:.0%} — 35% 초과 집중"
        )

    # 데이터 한계: expanded가 baseline 밖 새 심볼을 못 더하면 진짜 확장 검증 불가.
    if base and expanded and not (set(expanded.present) - set(base.present)):
        warnings.append(
            "expanded가 baseline 밖 새 심볼을 추가하지 못함 — 확장 후보 데이터 부재로 진짜 확장 검증 불가(과대 주장 금지)"
        )
    if expanded and expanded.missing:
        warnings.append(f"expanded에서 {len(expanded.missing)}개 심볼 데이터 없음·스킵: {', '.join(expanded.missing)}")

    # MU 의존도: baseline vs no_mu PnL 변화.
    if base and no_mu and base.total_pnl and no_mu.total_pnl is not None:
        drop = base.total_pnl - no_mu.total_pnl
        if base.total_pnl > 0 and drop / base.total_pnl >= 0.25:
            warnings.append(
                f"MU 제거 시 총손익 {drop / base.total_pnl:.0%} 감소({base.total_pnl:.0f}→{no_mu.total_pnl:.0f}) — MU 의존"
            )

    return UniverseBiasReport(variants=variants, warnings=tuple(warnings))


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _md_row(v: UniverseResult) -> str:
    return (
        f"| {v.name} | {len(v.present)} | {_pct(v.cumulative_return)} | "
        f"{'n/a' if v.total_pnl is None else f'{v.total_pnl:.2f}'} | {_pct(v.max_drawdown)} | "
        f"{_pct(v.return_over_mdd, '{:.2f}')} | {_pct(v.win_rate, '{:.0%}')} | {v.trades} | "
        f"{v.top1_symbol or '-'} {_pct(v.top1_share, '{:.0%}')} | {_pct(v.top3_share, '{:.0%}')} | "
        f"{v.best_symbol or '-'}/{v.worst_symbol or '-'} | {v.beats_spy} | {v.beats_qqq} |"
    )


def format_universe_bias_markdown(report: UniverseBiasReport) -> str:
    """마크다운 리포트(reports/universe_bias_test.md). 측정 보조 — 매매 미사용."""
    base = _get(report.variants, "baseline")
    expanded = _get(report.variants, "expanded")
    no_mu = _get(report.variants, "expanded_no_mu")
    no_top3 = _get(report.variants, "expanded_no_top3")

    lines: list[str] = []
    lines.append("# Universe Expansion / Bias Test (측정 - 실주문 없음, next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. "
                 "기본 유니버스/베이스라인 파라미터 미변경. 레버리지 ETF 미혼합.")
    lines.append("")
    lines.append("## 변형 비교")
    lines.append("")
    lines.append("| variant | nSym | return | total PnL | MDD | ret/MDD | win | trades "
                 "| top1 share | top3 share | best/worst | >SPY | >QQQ |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in report.variants:
        lines.append(_md_row(v))
    lines.append("")

    lines.append("## 벤치마크")
    lines.append("")
    if base:
        lines.append(f"- baseline return {_pct(base.cumulative_return)} vs "
                     f"SPY {_pct(base.spy_return)} / QQQ {_pct(base.qqq_return)} / "
                     f"equal-weight {_pct(base.eq_return)}")
    lines.append("")

    lines.append("## 분기 PnL (baseline)")
    lines.append("")
    if base and base.quarterly:
        for label, pnl in base.quarterly:
            lines.append(f"- {label}: {pnl:.2f}")
    lines.append("")

    lines.append("## 결측·무거래 심볼")
    lines.append("")
    for v in report.variants:
        lines.append(f"- **{v.name}**: missing {len(v.missing)} "
                     f"({', '.join(v.missing) if v.missing else '없음'}); "
                     f"zero-trade {len(v.zero_trade)} "
                     f"({', '.join(v.zero_trade) if v.zero_trade else '없음'})")
    lines.append("")

    lines.append("## 질문에 대한 답 (정직, 과대 주장 금지)")
    lines.append("")
    # 1. 20심볼 밖에서 붕괴?
    if base and expanded:
        new_syms = sorted(set(expanded.present) - set(base.present))
        if not new_syms:
            lines.append("- **20심볼 밖에서 붕괴하나?** 판정 불가 — 확장 후보가 baseline 밖 새 심볼을 "
                         "추가하지 못함(로컬 데이터 부재, 38개 스킵). 데이터 확보 전엔 답할 수 없음.")
        else:
            verdict = "유지" if (expanded.cumulative_return or 0) > 0 else "붕괴"
            lines.append(f"- **20심볼 밖에서 붕괴하나?** 새 심볼 {', '.join(new_syms)} 추가 시 "
                         f"expanded return {_pct(expanded.cumulative_return)} → {verdict}.")
    # 2. 집중도
    if base:
        msg = f"baseline top1 {_pct(base.top1_share, '{:.0%}')}, top3 {_pct(base.top3_share, '{:.0%}')}"
        if no_top3 is not None:
            msg += f"; top3 제외 시 return {_pct(no_top3.cumulative_return)}"
        lines.append(f"- **집중도는 개선/악화?** {msg}.")
    # 3. MU
    if base and no_mu:
        if base.total_pnl and no_mu.total_pnl is not None:
            drop = base.total_pnl - no_mu.total_pnl
            frac = drop / base.total_pnl if base.total_pnl else 0
            lines.append(f"- **MU가 여전히 결과를 끌고 가나?** MU 제거 시 PnL {base.total_pnl:.0f}→"
                         f"{no_mu.total_pnl:.0f} ({frac:+.0%}). "
                         f"{'여전히 MU 의존' if frac >= 0.25 else 'MU 의존 제한적'}.")
    # 4. 로버스트니스 vs 노이즈
    if base and expanded and not (set(expanded.present) - set(base.present)):
        lines.append("- **확장이 로버스트니스를 더하나, 노이즈인가?** 현재 데이터로는 새 심볼 추가가 불가 — "
                     "추가 데이터 없이는 결론 불가. 인프라만 준비됨.")
    # 5. SPY/QQQ
    if base:
        lines.append(f"- **여전히 SPY/QQQ를 이기나?** beats SPY={base.beats_spy}, beats QQQ={base.beats_qqq}.")
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
