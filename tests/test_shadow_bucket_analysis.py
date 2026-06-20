"""shadow_bucket_analysis 테스트 (spec: specs/shadow_bucket_analysis.md).

섀도 스코어를 사분위 버킷으로 나눠 고점수 버킷이 더 좋은 성과를 내는지 평가(측정 전용). unscored 제외,
작은 표본 안전, 입력 불변. real_orders=0. 네트워크 없음.
"""

from types import SimpleNamespace

from agents.feature_shadow_score import ShadowScoreReport, ShadowTradeScore
from agents.shadow_bucket_analysis import (
    BucketStat,
    ShadowBucketReport,
    compute_shadow_bucket_analysis,
    format_shadow_bucket_analysis,
)
from agents.trade_diagnostics import TradeDiagnostics


def _ts(symbol, score, pnl):
    return ShadowTradeScore(
        symbol=symbol, entry_date="d", score=score, pnl=pnl,
        is_winner=(pnl > 0 if pnl is not None else None),
        missing_count=0,
    )


def _shadow(scores):
    """ShadowScoreReport 최소 구성(버킷 모듈은 .trades만 읽음)."""
    return ShadowScoreReport(
        trades=tuple(scores), num_scored=sum(1 for s in scores if s.score is not None),
        num_unscored=sum(1 for s in scores if s.score is None),
        winner_avg_score=None, loser_avg_score=None, separation=None,
        score_pnl_correlation=None, top_half_win_rate=None, bottom_half_win_rate=None,
        best_scored=None, worst_scored=None, warnings=(),
    )


def _empty_trade_diag():
    return TradeDiagnostics(
        trades=(), best_trade=None, worst_trade=None, drawdown=None,
        equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


def _bucket(report, name):
    return next(b for b in report.buckets if b.name == name)


# --- 버킷 배정 ---


def test_bucket_assignment_top_has_highest_scores():
    # 점수 1..8 → top 버킷은 최고 점수 2건(G,H).
    scores = [
        _ts("A", 1.0, -5.0), _ts("B", 2.0, -4.0), _ts("C", 3.0, -3.0), _ts("D", 4.0, -1.0),
        _ts("E", 5.0, 1.0), _ts("F", 6.0, 3.0), _ts("G", 7.0, 8.0), _ts("H", 8.0, 12.0),
    ]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    assert isinstance(rep, ShadowBucketReport)
    assert len(rep.buckets) == 4
    top = _bucket(rep, "top")
    bottom = _bucket(rep, "bottom")
    assert set(top.symbols) == {"G", "H"}
    assert set(bottom.symbols) == {"A", "B"}
    assert top.count == 2 and bottom.count == 2
    assert rep.real_orders_placed == 0


def test_bucket_stats_compute_correctly():
    scores = [
        _ts("A", 1.0, -10.0), _ts("B", 2.0, -2.0),    # bottom
        _ts("C", 3.0, -1.0), _ts("D", 4.0, 1.0),      # lower-middle
        _ts("E", 5.0, 2.0), _ts("F", 6.0, 4.0),       # upper-middle
        _ts("G", 7.0, 6.0), _ts("H", 8.0, 10.0),      # top
    ]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    top = _bucket(rep, "top")
    assert top.count == 2
    assert top.win_rate == 1.0                 # 6,10 둘 다 승
    assert top.avg_pnl == 8.0                  # (6+10)/2
    assert top.median_pnl == 8.0
    assert top.total_pnl == 16.0
    assert top.avg_score == 7.5                # (7+8)/2
    bottom = _bucket(rep, "bottom")
    assert bottom.win_rate == 0.0              # -10,-2 둘 다 패


# --- 단조성 ---


def test_monotonic_outperformance_no_warning():
    # 점수와 pnl이 같은 방향 → 상위 버킷이 더 좋음.
    scores = [_ts(chr(65 + i), float(i), float(i) - 3.5) for i in range(8)]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    assert rep.monotonic_avg_pnl is True
    assert rep.top_minus_bottom_avg_pnl > 0
    assert not any("능가" in w or "단조" in w for w in rep.warnings)


def test_non_monotonic_emits_warning():
    # 상위 점수가 오히려 손실 → 비단조.
    scores = [
        _ts("A", 1.0, 10.0), _ts("B", 2.0, 9.0),    # bottom: 좋은 pnl
        _ts("C", 3.0, 5.0), _ts("D", 4.0, 4.0),
        _ts("E", 5.0, -2.0), _ts("F", 6.0, -3.0),
        _ts("G", 7.0, -8.0), _ts("H", 8.0, -10.0),  # top: 나쁜 pnl
    ]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    assert rep.top_minus_bottom_avg_pnl < 0
    assert any("능가" in w for w in rep.warnings)


# --- fail-safe ---


def test_small_sample_safe_with_warning():
    scores = [_ts("A", 1.0, -5.0), _ts("B", 2.0, 5.0)]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    assert rep.num_scored == 2
    assert any("표본" in w for w in rep.warnings)
    # 빈 버킷은 count 0, 통계 None — 예외 없음.
    for b in rep.buckets:
        if b.count == 0:
            assert b.avg_pnl is None and b.symbols == ()


def test_unscored_trades_excluded_safely():
    scores = [
        _ts("A", 1.0, -5.0), _ts("B", 2.0, -2.0), _ts("C", 3.0, 2.0), _ts("D", 4.0, 5.0),
        _ts("X", None, 3.0), _ts("Y", None, -1.0),     # unscored
    ]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    assert rep.num_scored == 4
    assert rep.num_unscored == 2
    all_syms = {s for b in rep.buckets for s in b.symbols}
    assert "X" not in all_syms and "Y" not in all_syms


def test_no_trades_safe():
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow([]))
    assert rep.num_scored == 0
    assert all(b.count == 0 for b in rep.buckets)
    assert rep.top_minus_bottom_avg_pnl is None
    assert rep.real_orders_placed == 0


def test_inputs_not_mutated():
    scores = [_ts("A", 1.0, -5.0), _ts("B", 2.0, 5.0)]
    sr = _shadow(scores)
    td = _empty_trade_diag()
    trades_before = sr.trades
    td_trades_before = td.trades
    compute_shadow_bucket_analysis(td, sr)
    assert sr.trades == trades_before
    assert td.trades == td_trades_before


def test_format_contains_sections():
    scores = [_ts(chr(65 + i), float(i), float(i) - 3.5) for i in range(8)]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), _shadow(scores))
    text = format_shadow_bucket_analysis(rep)
    assert "Bucket" in text
    assert "top" in text
    assert "win_rate" in text.lower()
    assert "real_orders_placed : 0" in text


def test_duck_typed_shadow_report_accepted():
    # .trades만 읽으므로 SimpleNamespace도 동작(결합도 낮음 확인).
    scores = [_ts("A", 1.0, -5.0), _ts("B", 2.0, 5.0)]
    rep = compute_shadow_bucket_analysis(_empty_trade_diag(), SimpleNamespace(trades=tuple(scores)))
    assert rep.num_scored == 2
