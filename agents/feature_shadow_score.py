"""피처 섀도 스코어 — 기존 피처로 투명한 점수를 만들어 승/패 분리력을 사후 평가한다(순수 측정).

trade_diagnostics(트레이드 leg+pnl) + feature_diagnostics((symbol,entry_date)별 FeatureSnapshot)만
읽는다. 고정 가중치의 투명한 가중합일 뿐 학습/튜닝이 아니며, 이 점수를 매수/매도/사이징에 절대 쓰지
않는다. "피처 랭킹이 승자를 위로 올렸을까?"를 사후에 보는 용도다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/feature_shadow_score.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# 투명 가중치(고정 — 학습 아님). positive 가산.
W_MOMENTUM = 1.0
W_RETURN_1M = 0.5
W_RETURN_3M = 0.5
W_RELATIVE_STRENGTH = 0.8
W_VOLUME_EXCESS = 0.2     # (volume_ratio_20d - 1) 에 곱.
W_ABOVE_20MA = 0.3
W_RETURN_6M = 0.1         # 보이되 작게(과대평가 금지).
# caution 감점.
P_ATR = 2.0
ATR_REF = 0.05            # atr_pct가 5% 초과한 만큼만 감점.
P_DISTANCE = 1.0
DIST_REF = 0.10          # 고점 대비 10% 초과 하락분만 감점.
P_MISSING = 0.1          # missing_fields 1개당.


@dataclass(frozen=True)
class ShadowTradeScore:
    """트레이드 1건의 섀도 스코어(스냅샷 None이면 score=None)."""

    symbol: str
    entry_date: str | None
    score: float | None
    pnl: float | None
    is_winner: bool | None
    missing_count: int


@dataclass(frozen=True)
class ShadowScoreReport:
    """섀도 스코어 분석 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    trades: tuple[ShadowTradeScore, ...]
    num_scored: int
    num_unscored: int
    winner_avg_score: float | None
    loser_avg_score: float | None
    separation: float | None
    score_pnl_correlation: float | None
    top_half_win_rate: float | None
    bottom_half_win_rate: float | None
    best_scored: ShadowTradeScore | None
    worst_scored: ShadowTradeScore | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _score(snap) -> float | None:
    """FeatureSnapshot → 투명 가중합 점수. None 피처는 기여 0(안전). 스냅샷 None이면 None."""
    if snap is None:
        return None

    def val(name: str) -> float:
        x = getattr(snap, name)
        return float(x) if x is not None else 0.0

    score = (
        W_MOMENTUM * val("momentum_score")
        + W_RETURN_1M * val("return_1m")
        + W_RETURN_3M * val("return_3m")
        + W_RELATIVE_STRENGTH * val("relative_strength")
        + W_ABOVE_20MA * (1.0 if snap.price_above_20ma else 0.0)
        + W_RETURN_6M * val("return_6m")
    )
    if snap.volume_ratio_20d is not None:
        score += W_VOLUME_EXCESS * (snap.volume_ratio_20d - 1.0)
    if snap.atr_pct is not None:
        score -= P_ATR * max(0.0, snap.atr_pct - ATR_REF)
    if snap.distance_from_high is not None:
        score -= P_DISTANCE * max(0.0, (-snap.distance_from_high) - DIST_REF)
    score -= P_MISSING * len(snap.missing_fields)
    return score


def _avg(values):
    return statistics.fmean(values) if values else None


def _correlation(xs, ys):
    """score-pnl 단순 상관. 샘플 < 2 또는 분산 0이면 None."""
    if len(xs) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def compute_feature_shadow_score(trade_diag, feature_diag) -> ShadowScoreReport:
    """승/패 트레이드의 섀도 스코어와 분리력을 산출한다(읽기 전용 — 입력 불변)."""
    index = {(r.symbol, r.context_date): r.snapshot for r in feature_diag.rows}

    scores: list[ShadowTradeScore] = []
    for t in trade_diag.trades:
        if t.pnl is None:
            continue
        snap = index.get((t.symbol, t.entry_date))
        missing = len(snap.missing_fields) if snap is not None else 0
        scores.append(ShadowTradeScore(
            symbol=t.symbol, entry_date=t.entry_date, score=_score(snap),
            pnl=t.pnl, is_winner=(t.pnl > 0), missing_count=missing,
        ))

    scored = [s for s in scores if s.score is not None]
    winner_scores = [s.score for s in scored if s.is_winner]
    loser_scores = [s.score for s in scored if not s.is_winner]

    winner_avg = _avg(winner_scores)
    loser_avg = _avg(loser_scores)
    separation = (winner_avg - loser_avg) if (winner_avg is not None and loser_avg is not None) else None

    corr = _correlation([s.score for s in scored], [s.pnl for s in scored])

    # 점수 중앙 분할 상·하위 승률(랭킹 분리력).
    top_rate = bottom_rate = None
    if len(scored) >= 2:
        ordered = sorted(scored, key=lambda s: s.score)
        half = len(ordered) // 2
        bottom = ordered[:half]
        top = ordered[len(ordered) - half:]
        bottom_rate = _avg([1.0 if s.is_winner else 0.0 for s in bottom])
        top_rate = _avg([1.0 if s.is_winner else 0.0 for s in top])

    best = max(scored, key=lambda s: s.score) if scored else None
    worst = min(scored, key=lambda s: s.score) if scored else None

    warnings: list[str] = []
    if separation is not None and separation <= 0:
        warnings.append("섀도 스코어가 승/패를 분리하지 못함 (winner_avg ≤ loser_avg)")
    if corr is not None and corr < 0:
        warnings.append(f"score-pnl 상관 음수 ({corr:.2f}) — 점수가 성과와 역방향")
    if top_rate is not None and bottom_rate is not None and top_rate <= bottom_rate:
        warnings.append("상위 점수 승률이 하위 점수 승률 이하 — 랭킹 분리력 약함")

    return ShadowScoreReport(
        trades=tuple(scores),
        num_scored=len(scored),
        num_unscored=len(scores) - len(scored),
        winner_avg_score=winner_avg,
        loser_avg_score=loser_avg,
        separation=separation,
        score_pnl_correlation=corr,
        top_half_win_rate=top_rate,
        bottom_half_win_rate=bottom_rate,
        best_scored=best,
        worst_scored=worst,
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.3f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_feature_shadow_score(report: ShadowScoreReport, *, max_rows: int = 50) -> str:
    """사람이 읽는 섀도 스코어 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Feature Shadow Score (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)
    lines.append(f"scored: {report.num_scored}  unscored: {report.num_unscored}")
    lines.append(
        f"winner_avg: {_fmt(report.winner_avg_score)}  loser_avg: {_fmt(report.loser_avg_score)}  "
        f"separation: {_fmt(report.separation)}"
    )
    lines.append(
        f"score-pnl correlation: {_fmt(report.score_pnl_correlation, '{:.2f}')}  "
        f"top/bottom win rate: {_fmt(report.top_half_win_rate, '{:.0%}')} / "
        f"{_fmt(report.bottom_half_win_rate, '{:.0%}')}"
    )

    if report.best_scored is not None:
        b = report.best_scored
        lines.append(
            f"best scored : {b.symbol} score={_fmt(b.score)} "
            f"pnl={_fmt(b.pnl, '{:.2f}')} {'WIN' if b.is_winner else 'LOSS'}"
        )
    if report.worst_scored is not None:
        w = report.worst_scored
        lines.append(
            f"worst scored: {w.symbol} score={_fmt(w.score)} "
            f"pnl={_fmt(w.pnl, '{:.2f}')} {'WIN' if w.is_winner else 'LOSS'}"
        )

    lines.append(f"  {'symbol':<8}{'date':<12}{'score':>9}{'pnl':>10}  outcome")
    for s in report.trades[:max_rows]:
        outcome = "n/a" if s.is_winner is None else ("WIN" if s.is_winner else "LOSS")
        lines.append(
            f"  {s.symbol:<8}{(s.entry_date or '-'):<12}{_fmt(s.score):>9}"
            f"{_fmt(s.pnl, '{:.2f}'):>10}  {outcome}"
            + (f"  (missing {s.missing_count})" if s.missing_count else "")
        )
    if len(report.trades) > max_rows:
        lines.append(f"  ... (+{len(report.trades) - max_rows} more)")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
