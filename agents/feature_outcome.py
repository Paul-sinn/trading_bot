"""피처-성과 분석 — 승리 vs 패배 트레이드의 진입 피처 차이를 본다(순수 측정).

trade_diagnostics(트레이드 leg+pnl) + feature_diagnostics((symbol,entry_date)별 FeatureSnapshot)만
읽는다. 상태/매매/veto를 바꾸지 않는다 — 피처는 아직 매수/매도 판단에 쓰지 않는다. "이 피처가 높을 때
이겼다/졌다"는 관찰을 요약할 뿐, 어떤 임계값도 강제하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/feature_outcome.md
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass

# 통계 대상 수치 피처 / 비율 대상 플래그(features.FeatureSnapshot 필드명과 일치).
NUMERIC_FEATURES = (
    "momentum_score", "return_1m", "return_3m", "return_6m",
    "relative_strength", "volume_ratio_20d", "atr_pct", "distance_from_high",
)
FLAG_FEATURES = ("price_above_20ma", "price_above_50ma", "ma20_above_ma50")

# 관찰 경고 임계값(판단 아님 — 리포트 강조용).
_DISTANCE_WARN = -0.10   # 최근 고점 대비 10%+ 하락 지점 진입.


@dataclass(frozen=True)
class FeatureStat:
    """한 수치 피처의 승/패 평균·중앙값(값 None인 leg는 제외)."""

    feature: str
    winner_mean: float | None
    winner_median: float | None
    loser_mean: float | None
    loser_median: float | None


@dataclass(frozen=True)
class FlagStat:
    """한 불리언 추세 플래그의 승/패 True 비율(스냅샷 없거나 None인 leg는 제외)."""

    feature: str
    winner_true_rate: float | None
    loser_true_rate: float | None


@dataclass(frozen=True)
class SymbolOutcome:
    """심볼별 성과 요약."""

    symbol: str
    wins: int
    losses: int
    total_pnl: float
    avg_momentum: float | None


@dataclass(frozen=True)
class FeatureOutcomeReport:
    """피처-성과 분석 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    winners: int
    losers: int
    neutral: int
    numeric_stats: tuple[FeatureStat, ...]
    flag_stats: tuple[FlagStat, ...]
    best_trade_features: object | None
    worst_trade_features: object | None
    symbol_summary: tuple[SymbolOutcome, ...]
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _snapshot_index(feature_diag):
    """(symbol, context_date) → FeatureSnapshot|None 조회표."""
    index: dict[tuple[str, str | None], object | None] = {}
    for row in feature_diag.rows:
        index[(row.symbol, row.context_date)] = row.snapshot
    return index


def _mean(values):
    return statistics.fmean(values) if values else None


def _median(values):
    return statistics.median(values) if values else None


def _numeric_stat(feature, win_snaps, lose_snaps) -> FeatureStat:
    """승/패 스냅샷 리스트에서 한 수치 피처의 평균/중앙값(None 제외)."""
    win_vals = [getattr(s, feature) for s in win_snaps if getattr(s, feature) is not None]
    lose_vals = [getattr(s, feature) for s in lose_snaps if getattr(s, feature) is not None]
    return FeatureStat(
        feature=feature,
        winner_mean=_mean(win_vals), winner_median=_median(win_vals),
        loser_mean=_mean(lose_vals), loser_median=_median(lose_vals),
    )


def _flag_stat(feature, win_snaps, lose_snaps) -> FlagStat:
    """승/패 스냅샷에서 한 플래그의 True 비율(None 제외)."""
    win_vals = [getattr(s, feature) for s in win_snaps if getattr(s, feature) is not None]
    lose_vals = [getattr(s, feature) for s in lose_snaps if getattr(s, feature) is not None]
    return FlagStat(
        feature=feature,
        winner_true_rate=(sum(win_vals) / len(win_vals)) if win_vals else None,
        loser_true_rate=(sum(lose_vals) / len(lose_vals)) if lose_vals else None,
    )


def _warnings(lose_pairs) -> tuple[str, ...]:
    """패배 트레이드 스냅샷에서 관찰 경고를 만든다(판단 아님)."""
    out: list[str] = []
    far = sum(
        1 for s in lose_pairs
        if s is not None and s.distance_from_high is not None
        and s.distance_from_high <= _DISTANCE_WARN
    )
    if far:
        out.append(
            f"패배 {far}건이 최근 고점 대비 {abs(_DISTANCE_WARN):.0%}+ 하락 지점에서 진입"
            " (distance_from_high 낮음)"
        )
    weak_rs = sum(
        1 for s in lose_pairs
        if s is not None and s.relative_strength is not None and s.relative_strength < 0
    )
    if weak_rs:
        out.append(f"패배 {weak_rs}건이 진입 시 상대강도 음수(relative_strength < 0)")
    weak_mom = sum(
        1 for s in lose_pairs
        if s is not None and s.momentum_score is not None and s.momentum_score < 0
    )
    if weak_mom:
        out.append(f"패배 {weak_mom}건이 진입 시 모멘텀 음수(momentum_score < 0)")
    return tuple(out)


def compute_feature_outcome(trade_diag, feature_diag) -> FeatureOutcomeReport:
    """승/패 트레이드의 진입 피처 차이를 분석한다(읽기 전용 — 입력 불변)."""
    index = _snapshot_index(feature_diag)

    win_snaps: list[object] = []
    lose_snaps: list[object] = []
    lose_all: list[object | None] = []      # 경고용(None 포함).
    winners = losers = neutral = 0
    sym_wins: Counter[str] = Counter()
    sym_losses: Counter[str] = Counter()
    sym_pnl: Counter[str] = Counter()
    sym_moms: dict[str, list[float]] = {}

    for t in trade_diag.trades:
        if t.pnl is None:
            continue
        snap = index.get((t.symbol, t.entry_date))
        if t.pnl > 0:
            winners += 1
            sym_wins[t.symbol] += 1
            if snap is not None:
                win_snaps.append(snap)
        elif t.pnl < 0:
            losers += 1
            sym_losses[t.symbol] += 1
            lose_all.append(snap)
            if snap is not None:
                lose_snaps.append(snap)
        else:
            neutral += 1
            continue
        sym_pnl[t.symbol] += t.pnl
        if snap is not None and snap.momentum_score is not None:
            sym_moms.setdefault(t.symbol, []).append(snap.momentum_score)

    numeric_stats = tuple(_numeric_stat(f, win_snaps, lose_snaps) for f in NUMERIC_FEATURES)
    flag_stats = tuple(_flag_stat(f, win_snaps, lose_snaps) for f in FLAG_FEATURES)

    symbols = set(sym_wins) | set(sym_losses)
    symbol_summary = tuple(sorted(
        (
            SymbolOutcome(
                symbol=sym, wins=sym_wins[sym], losses=sym_losses[sym],
                total_pnl=float(sym_pnl[sym]),
                avg_momentum=_mean(sym_moms.get(sym, [])),
            )
            for sym in symbols
        ),
        key=lambda s: s.total_pnl, reverse=True,
    ))

    def _lookup(leg):
        return index.get((leg.symbol, leg.entry_date)) if leg is not None else None

    return FeatureOutcomeReport(
        winners=winners, losers=losers, neutral=neutral,
        numeric_stats=numeric_stats, flag_stats=flag_stats,
        best_trade_features=_lookup(trade_diag.best_trade),
        worst_trade_features=_lookup(trade_diag.worst_trade),
        symbol_summary=symbol_summary,
        warnings=_warnings(lose_all),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_feature_outcome(report: FeatureOutcomeReport) -> str:
    """사람이 읽는 피처-성과 텍스트(측정 보조 — 판단 아님)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Feature Outcome Analysis (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)
    lines.append(f"winners: {report.winners}  losers: {report.losers}  neutral: {report.neutral}")

    lines.append(f"  {'feature':<20}{'win_mean':>11}{'win_med':>11}{'lose_mean':>11}{'lose_med':>11}")
    for s in report.numeric_stats:
        lines.append(
            f"  {s.feature:<20}{_fmt(s.winner_mean):>11}{_fmt(s.winner_median):>11}"
            f"{_fmt(s.loser_mean):>11}{_fmt(s.loser_median):>11}"
        )

    lines.append("trend flags (True rate):")
    for f in report.flag_stats:
        lines.append(
            f"  {f.feature:<20}win {_fmt(f.winner_true_rate)}  lose {_fmt(f.loser_true_rate)}"
        )

    bf, wf = report.best_trade_features, report.worst_trade_features
    if bf is not None:
        lines.append(
            f"best trade : {bf.symbol} mom={_fmt(bf.momentum_score)} rs={_fmt(bf.relative_strength)} "
            f"fromHi={_fmt(bf.distance_from_high)}"
        )
    if wf is not None:
        lines.append(
            f"worst trade: {wf.symbol} mom={_fmt(wf.momentum_score)} rs={_fmt(wf.relative_strength)} "
            f"fromHi={_fmt(wf.distance_from_high)}"
        )

    if report.symbol_summary:
        lines.append("symbol outcomes (by pnl):")
        for s in report.symbol_summary[:15]:
            lines.append(
                f"  {s.symbol:<8}W{s.wins} L{s.losses}  pnl={s.total_pnl:>10.2f}  "
                f"avg_mom={_fmt(s.avg_momentum)}"
            )

    if report.warnings:
        lines.append("notable warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
