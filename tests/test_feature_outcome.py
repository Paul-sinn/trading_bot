"""feature_outcome 테스트 (spec: specs/feature_outcome.md).

승/패 트레이드의 진입 피처 차이를 분석(측정 전용). 스냅샷 없음/None 안전. 입력 불변(매매/veto 안 바뀜).
real_orders=0. 네트워크 없음.
"""

from agents.feature_diagnostics import FeatureDiagnostics, FeatureRow
from agents.feature_outcome import (
    FeatureOutcomeReport,
    compute_feature_outcome,
    format_feature_outcome,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg
from algorithms.features import FeatureSnapshot


def _snap(symbol, date, **over):
    base = dict(
        symbol=symbol, as_of=date, return_1m=0.05, return_3m=0.10, return_6m=0.15,
        momentum_score=0.08, relative_strength=0.05, volume_ratio_20d=2.0,
        atr_pct=0.03, distance_from_high=-0.02, price_above_20ma=True,
        price_above_50ma=True, ma20_above_ma50=True, missing_fields=(),
    )
    base.update(over)
    return FeatureSnapshot(**base)


def _leg(symbol, date, pnl):
    return TradeLeg(
        symbol=symbol, entry_date=date, exit_date="2025-12-31",
        entry_price=100.0, exit_price=100.0 + pnl, qty=1.0,
        pnl=pnl, pnl_pct=pnl / 100.0, exit_reason="sell",
    )


def _trade_diag(legs, *, best=None, worst=None):
    return TradeDiagnostics(
        trades=tuple(legs), best_trade=best, worst_trade=worst, drawdown=None,
        equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


def _feature_diag(snaps):
    rows = tuple(
        FeatureRow(symbol=s.symbol, context_date=s.as_of, snapshot=s, note=None)
        for s in snaps
    )
    return FeatureDiagnostics(rows=rows)


# --- 집계 ---


def test_winners_losers_aggregate_correctly():
    legs = [
        _leg("NVDA", "2025-01-02", +20.0),   # 승: 높은 모멘텀
        _leg("MSFT", "2025-01-03", +10.0),
        _leg("AMD", "2025-01-06", -15.0),    # 패: 낮은 모멘텀
        _leg("GOOG", "2025-01-07", -5.0),
    ]
    snaps = [
        _snap("NVDA", "2025-01-02", momentum_score=0.30),
        _snap("MSFT", "2025-01-03", momentum_score=0.20),
        _snap("AMD", "2025-01-06", momentum_score=-0.05),
        _snap("GOOG", "2025-01-07", momentum_score=0.02),
    ]
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    assert isinstance(rep, FeatureOutcomeReport)
    assert rep.winners == 2
    assert rep.losers == 2
    mom = next(s for s in rep.numeric_stats if s.feature == "momentum_score")
    assert mom.winner_mean == 0.25       # (0.30+0.20)/2
    assert mom.loser_mean < mom.winner_mean
    assert rep.real_orders_placed == 0


def test_flag_true_rate():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    snaps = [
        _snap("A", "d1", price_above_50ma=True),
        _snap("B", "d2", price_above_50ma=False),
    ]
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    flag = next(f for f in rep.flag_stats if f.feature == "price_above_50ma")
    assert flag.winner_true_rate == 1.0
    assert flag.loser_true_rate == 0.0


def test_missing_snapshot_handled_safely():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    # B는 스냅샷 None(계산 불가) — 카운트는 되고 피처 집계서 제외.
    rows = (
        FeatureRow("A", "d1", _snap("A", "d1", momentum_score=0.30), None),
        FeatureRow("B", "d2", None, "가격 데이터 없음"),
    )
    rep = compute_feature_outcome(_trade_diag(legs), FeatureDiagnostics(rows=rows))
    assert rep.winners == 1 and rep.losers == 1
    mom = next(s for s in rep.numeric_stats if s.feature == "momentum_score")
    assert mom.winner_mean == 0.30
    assert mom.loser_mean is None        # 패자 스냅샷 None → 통계 없음


def test_missing_feature_value_excluded():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", +5.0)]
    snaps = [
        _snap("A", "d1", return_6m=0.20),
        _snap("B", "d2", return_6m=None),    # 데이터 부족
    ]
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    r6 = next(s for s in rep.numeric_stats if s.feature == "return_6m")
    assert r6.winner_mean == 0.20          # None은 평균서 제외


def test_best_worst_snapshot_lookup():
    best = _leg("NVDA", "2025-01-02", +20.0)
    worst = _leg("AMD", "2025-01-06", -15.0)
    legs = [best, worst]
    snaps = [
        _snap("NVDA", "2025-01-02", momentum_score=0.30),
        _snap("AMD", "2025-01-06", momentum_score=-0.05),
    ]
    rep = compute_feature_outcome(
        _trade_diag(legs, best=best, worst=worst), _feature_diag(snaps)
    )
    assert rep.best_trade_features is not None
    assert rep.best_trade_features.symbol == "NVDA"
    assert rep.worst_trade_features.symbol == "AMD"


def test_symbol_summary():
    legs = [
        _leg("NVDA", "d1", +20.0),
        _leg("NVDA", "d2", -5.0),
        _leg("AMD", "d3", -15.0),
    ]
    snaps = [_snap("NVDA", "d1"), _snap("NVDA", "d2"), _snap("AMD", "d3")]
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    by = {s.symbol: s for s in rep.symbol_summary}
    assert by["NVDA"].wins == 1 and by["NVDA"].losses == 1
    assert by["NVDA"].total_pnl == 15.0
    assert by["AMD"].losses == 1


def test_distance_from_high_warning():
    legs = [_leg("AMD", "d1", -15.0)]
    snaps = [_snap("AMD", "d1", distance_from_high=-0.25)]   # 고점 -25%서 진입 후 손실
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    assert any("distance_from_high" in w for w in rep.warnings)


def test_no_trades_safe():
    rep = compute_feature_outcome(_trade_diag([]), _feature_diag([]))
    assert rep.winners == 0 and rep.losers == 0
    assert rep.numeric_stats != ()       # 통계 행은 있되 값은 None
    assert all(s.winner_mean is None for s in rep.numeric_stats)
    assert rep.real_orders_placed == 0


def test_inputs_not_mutated():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    td = _trade_diag(legs)
    fd = _feature_diag([_snap("A", "d1"), _snap("B", "d2")])
    trades_before = td.trades
    rows_before = fd.rows
    compute_feature_outcome(td, fd)
    assert td.trades == trades_before     # 입력 불변(매매/veto 안 바뀜)
    assert fd.rows == rows_before


def test_format_contains_sections():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    snaps = [_snap("A", "d1"), _snap("B", "d2")]
    rep = compute_feature_outcome(_trade_diag(legs), _feature_diag(snaps))
    text = format_feature_outcome(rep)
    assert "Feature Outcome" in text
    assert "winners" in text.lower()
    assert "real_orders_placed : 0" in text
