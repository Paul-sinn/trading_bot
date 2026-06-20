"""섀도 스코어 버킷 분석 — 점수 사분위별 성과를 보고 "고점수=좋은 결과"가 일관적인지 본다(순수 측정).

feature_shadow_score의 ShadowScoreReport.trades(점수+pnl)만 읽는다. 상태/매매/veto를 바꾸지 않고,
점수/버킷을 매수/매도/사이징에 절대 쓰지 않는다. 단조성 점검은 관찰일 뿐 어떤 임계값도 강제하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/shadow_bucket_analysis.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# 사분위 인덱스(오름차순 rank 기준) → 이름. 0=최저점수, 3=최고점수.
_BUCKET_NAMES = ("bottom", "lower-middle", "upper-middle", "top")
_SMALL_SAMPLE = 4   # 버킷 4개를 채우려면 최소 4건.


@dataclass(frozen=True)
class BucketStat:
    """한 점수 버킷의 성과 요약. 빈 버킷은 count=0, 통계 None."""

    name: str
    count: int
    win_rate: float | None
    avg_pnl: float | None
    median_pnl: float | None
    total_pnl: float
    avg_score: float | None
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ShadowBucketReport:
    """섀도 스코어 버킷 분석 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    buckets: tuple[BucketStat, ...]      # top → bottom 순서.
    num_scored: int
    num_unscored: int
    monotonic_avg_pnl: bool | None
    monotonic_win_rate: bool | None
    top_minus_bottom_avg_pnl: float | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _assign(scored):
    """score 오름차순 rank로 4개 버킷에 배정. {0:bottom,...,3:top} 리스트 매핑."""
    buckets: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
    n = len(scored)
    if n == 0:
        return buckets
    ordered = sorted(scored, key=lambda t: t.score)
    for rank, t in enumerate(ordered):
        idx = min(3, rank * 4 // n)
        buckets[idx].append(t)
    return buckets


def _stat(name, members) -> BucketStat:
    """버킷 멤버(ShadowTradeScore)들로 통계를 만든다."""
    if not members:
        return BucketStat(name=name, count=0, win_rate=None, avg_pnl=None,
                          median_pnl=None, total_pnl=0.0, avg_score=None, symbols=())
    pnls = [m.pnl for m in members if m.pnl is not None]
    wins = sum(1 for m in members if m.is_winner)
    return BucketStat(
        name=name,
        count=len(members),
        win_rate=wins / len(members),
        avg_pnl=statistics.fmean(pnls) if pnls else None,
        median_pnl=statistics.median(pnls) if pnls else None,
        total_pnl=float(sum(pnls)),
        avg_score=statistics.fmean([m.score for m in members]),
        symbols=tuple(sorted({m.symbol for m in members})),
    )


def _is_non_decreasing(values) -> bool:
    return all(a <= b for a, b in zip(values, values[1:]))


def compute_shadow_bucket_analysis(trade_diag, shadow_report) -> ShadowBucketReport:
    """섀도 스코어를 사분위 버킷으로 나눠 성과 단조성을 본다(읽기 전용 — 입력 불변).

    trade_diag는 인터페이스 일관성용이며, pnl/점수는 shadow_report.trades에서 가져온다.
    """
    trades = list(shadow_report.trades)
    scored = [t for t in trades if t.score is not None]
    num_unscored = len(trades) - len(scored)

    assigned = _assign(scored)
    # 저장은 top→bottom 순서(인덱스 3,2,1,0).
    ordered_idx = (3, 2, 1, 0)
    buckets = tuple(_stat(_BUCKET_NAMES[i], assigned[i]) for i in ordered_idx)

    # 단조성: 비어있지 않은 버킷을 bottom→top 순서로.
    asc_nonempty = [_stat(_BUCKET_NAMES[i], assigned[i]) for i in (0, 1, 2, 3) if assigned[i]]
    avg_seq = [b.avg_pnl for b in asc_nonempty if b.avg_pnl is not None]
    win_seq = [b.win_rate for b in asc_nonempty if b.win_rate is not None]
    monotonic_avg = _is_non_decreasing(avg_seq) if len(avg_seq) >= 2 else None
    monotonic_win = _is_non_decreasing(win_seq) if len(win_seq) >= 2 else None

    top = next((b for b in buckets if b.name == "top"), None)
    bottom = next((b for b in buckets if b.name == "bottom"), None)
    if top is not None and bottom is not None and top.avg_pnl is not None and bottom.avg_pnl is not None:
        top_minus_bottom = top.avg_pnl - bottom.avg_pnl
    else:
        top_minus_bottom = None

    warnings: list[str] = []
    if len(scored) < _SMALL_SAMPLE:
        warnings.append(f"표본 부족(scored n={len(scored)} < {_SMALL_SAMPLE}) — 버킷 분석 신뢰도 낮음")
    if top_minus_bottom is not None and top_minus_bottom <= 0:
        warnings.append("상위 점수 버킷이 하위 버킷을 능가하지 못함 (top avg_pnl ≤ bottom)")
    if monotonic_avg is False:
        warnings.append("avg_pnl이 버킷 점수 순으로 단조 증가하지 않음 (랭킹 분리력 약함)")

    return ShadowBucketReport(
        buckets=buckets,
        num_scored=len(scored),
        num_unscored=num_unscored,
        monotonic_avg_pnl=monotonic_avg,
        monotonic_win_rate=monotonic_win,
        top_minus_bottom_avg_pnl=top_minus_bottom,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_shadow_bucket_analysis(report: ShadowBucketReport) -> str:
    """사람이 읽는 버킷 분석 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Shadow Score Bucket Analysis (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)
    lines.append(f"scored: {report.num_scored}  unscored: {report.num_unscored}")
    lines.append(
        f"  {'bucket':<14}{'n':>4}{'win_rate':>10}{'avg_pnl':>10}{'med_pnl':>10}"
        f"{'total':>10}{'avg_score':>11}  symbols"
    )
    for b in report.buckets:
        syms = ", ".join(b.symbols[:8]) + ("…" if len(b.symbols) > 8 else "")
        lines.append(
            f"  {b.name:<14}{b.count:>4}{_fmt(b.win_rate, '{:.0%}'):>10}"
            f"{_fmt(b.avg_pnl):>10}{_fmt(b.median_pnl):>10}{_fmt(b.total_pnl):>10}"
            f"{_fmt(b.avg_score, '{:.3f}'):>11}  {syms}"
        )

    lines.append(
        f"monotonic avg_pnl: {report.monotonic_avg_pnl}  "
        f"monotonic win_rate: {report.monotonic_win_rate}  "
        f"top-bottom avg_pnl: {_fmt(report.top_minus_bottom_avg_pnl)}"
    )
    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
