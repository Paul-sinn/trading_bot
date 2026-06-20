"""섀도 필터 What-if 분석 — 저점수 트레이드를 걸렀다면 성과가 어땠을지 고정 필터로 추정한다(순수 측정).

feature_shadow_score의 ShadowScoreReport.trades(점수+pnl+symbol+entry_date)만 읽는다. 실 시뮬/매매/veto를
바꾸지 않고, 점수/필터를 실제 매수/매도/사이징에 절대 쓰지 않는다. 임계값 최적화 없음(분위수/0 같은
자연 경계만) — 과적합 회피.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/shadow_whatif.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

_CONCENTRATION_SHARE = 0.6    # 유지 집합 양수손익의 60%+가 한 심볼이면 집중 경고.
_DEPENDENCE_DROP = 0.5        # LOO가 actual 총손익을 50%+ 떨어뜨리면 의존 경고.


@dataclass(frozen=True)
class FilterScenario:
    """한 What-if 필터의 결과(actual 부분집합). 측정 보조 — 판단 아님."""

    name: str
    kept_count: int
    removed_count: int
    win_rate: float | None
    total_pnl: float
    avg_pnl: float | None
    mdd_proxy: float | None
    total_pnl_diff: float | None       # vs actual
    avg_pnl_diff: float | None
    symbols_kept: tuple[str, ...]
    symbols_removed: tuple[str, ...]
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    concentration_warning: str | None


@dataclass(frozen=True)
class ShadowWhatIfReport:
    """섀도 필터 What-if 묶음(측정 보조). real_orders_placed는 항상 0."""

    actual: FilterScenario
    scenarios: tuple[FilterScenario, ...]
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _mdd_proxy(trades) -> float | None:
    """진입일순 누적 실현손익의 최대 낙폭(달러 근사). entry_date/pnl 가용 시만."""
    dated = [t for t in trades if t.entry_date is not None and t.pnl is not None]
    if not dated:
        return None
    dated = sorted(dated, key=lambda t: t.entry_date)
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for t in dated:
        cum += t.pnl
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def _top_symbol_share(kept):
    """유지 집합에서 손익 1위 심볼과 그 양수손익 비중. 양수손익 없으면 share None."""
    pnl_by: dict[str, float] = {}
    for t in kept:
        if t.pnl is not None:
            pnl_by[t.symbol] = pnl_by.get(t.symbol, 0.0) + t.pnl
    if not pnl_by:
        return None, None
    top_sym = max(pnl_by, key=lambda s: pnl_by[s])
    pos_total = sum(v for v in pnl_by.values() if v > 0)
    if pos_total <= 0 or pnl_by[top_sym] <= 0:
        return top_sym, None
    return top_sym, pnl_by[top_sym] / pos_total


def _build_scenario(name, kept, all_scored, actual_total, actual_avg) -> FilterScenario:
    """유지 트레이드 집합으로 시나리오 통계를 만든다(actual 대비 차이 포함)."""
    kept_syms = {t.symbol for t in kept}
    kept_ids = {id(t) for t in kept}     # 값 동등이 아닌 동일성으로 분할(중복값 트레이드 안전).
    removed = [t for t in all_scored if id(t) not in kept_ids]
    removed_syms = {t.symbol for t in removed} - kept_syms

    pnls = [t.pnl for t in kept if t.pnl is not None]
    total = float(sum(pnls))
    avg = statistics.fmean(pnls) if pnls else None
    wins = sum(1 for t in kept if t.is_winner)
    win_rate = wins / len(kept) if kept else None

    top_sym, share = _top_symbol_share(kept)
    concentration = None
    if share is not None and share >= _CONCENTRATION_SHARE:
        concentration = f"유지 손익의 {share:.0%}가 {top_sym} — 단일 심볼 집중"

    return FilterScenario(
        name=name,
        kept_count=len(kept),
        removed_count=len(removed),
        win_rate=win_rate,
        total_pnl=total,
        avg_pnl=avg,
        mdd_proxy=_mdd_proxy(kept),
        total_pnl_diff=(total - actual_total) if actual_total is not None else None,
        avg_pnl_diff=(avg - actual_avg) if (avg is not None and actual_avg is not None) else None,
        symbols_kept=tuple(sorted(kept_syms)),
        symbols_removed=tuple(sorted(removed_syms)),
        top_symbol=top_sym,
        top_symbol_pnl_share=share,
        concentration_warning=concentration,
    )


def compute_shadow_whatif(trade_diag, shadow_report) -> ShadowWhatIfReport:
    """고정 What-if 필터로 성과 차이를 추정한다(읽기 전용 — 입력 불변).

    trade_diag는 인터페이스 일관성용. 점수/pnl은 shadow_report.trades에서 가져온다.
    """
    scored = [t for t in shadow_report.trades if t.score is not None]
    n = len(scored)
    ordered = sorted(scored, key=lambda t: t.score)

    # actual 기준선(필터 없음).
    actual = _build_scenario("actual", scored, scored, None, None)
    actual_total = actual.total_pnl
    actual_avg = actual.avg_pnl

    def _keep_from_rank(frac):
        thr = frac * n
        return [t for rank, t in enumerate(ordered) if rank >= thr]

    scenarios: list[FilterScenario] = [
        _build_scenario("keep-top-quartile", _keep_from_rank(0.75), scored, actual_total, actual_avg),
        _build_scenario("keep-top-half", _keep_from_rank(0.5), scored, actual_total, actual_avg),
        _build_scenario("drop-bottom-quartile", _keep_from_rank(0.25), scored, actual_total, actual_avg),
        _build_scenario(
            "drop-negative-scores",
            [t for t in scored if t.score >= 0], scored, actual_total, actual_avg,
        ),
    ]

    # leave-one-symbol-out (특히 AMD): 한 심볼 의존도 점검.
    for sym in sorted({t.symbol for t in scored}):
        scenarios.append(_build_scenario(
            f"drop-{sym}", [t for t in scored if t.symbol != sym], scored, actual_total, actual_avg,
        ))

    warnings: list[str] = []
    # LOO 의존: 한 심볼 제거가 actual 총손익을 크게 떨어뜨리면.
    if actual_total > 0:
        for s in scenarios:
            if s.name.startswith("drop-") and s.total_pnl_diff is not None and len(s.symbols_removed) == 1:
                drop_frac = -s.total_pnl_diff / actual_total
                if drop_frac >= _DEPENDENCE_DROP:
                    warnings.append(
                        f"성과가 {s.symbols_removed[0]}에 집중 — 제거 시 총손익 {drop_frac:.0%} 감소"
                    )

    return ShadowWhatIfReport(actual=actual, scenarios=tuple(scenarios), warnings=tuple(warnings))


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _row(s: FilterScenario) -> str:
    return (
        f"  {s.name:<22}{s.kept_count:>4}{_fmt(s.win_rate, '{:.0%}'):>9}"
        f"{_fmt(s.total_pnl):>10}{_fmt(s.avg_pnl):>9}{_fmt(s.mdd_proxy):>9}"
        f"{_fmt(s.total_pnl_diff, '{:+.2f}'):>10}"
    )


def format_shadow_whatif(report: ShadowWhatIfReport) -> str:
    """사람이 읽는 What-if 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Shadow Filter What-if (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)
    lines.append(
        f"  {'scenario':<22}{'kept':>4}{'win':>9}{'total':>10}{'avg':>9}{'mddX':>9}{'Δtotal':>10}"
    )
    lines.append(_row(report.actual))
    for s in report.scenarios:
        lines.append(_row(s))
        if s.concentration_warning:
            lines.append(f"      ! {s.concentration_warning}")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append("  (mddX = 진입일순 누적 실현손익 낙폭 근사 — 포지션 레벨 MDD 아님)")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
