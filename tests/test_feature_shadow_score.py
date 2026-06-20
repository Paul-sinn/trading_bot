"""feature_shadow_score 테스트 (spec: specs/feature_shadow_score.md).

기존 피처로 투명한 섀도 스코어를 만들어 승/패 분리력을 사후 평가(측정 전용). 점수는 매매에 쓰지 않음.
None/missing 안전. 입력 불변. real_orders=0. 네트워크 없음.
"""

from agents.feature_diagnostics import FeatureDiagnostics, FeatureRow
from agents.feature_shadow_score import (
    ShadowScoreReport,
    ShadowTradeScore,
    compute_feature_shadow_score,
    format_feature_shadow_score,
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


def _score_of(report, symbol):
    return next(t.score for t in report.trades if t.symbol == symbol)


# --- 점수 계산 ---


def test_score_monotonic_in_momentum():
    legs = [_leg("HI", "d1", +5.0), _leg("LO", "d2", +5.0)]
    snaps = [_snap("HI", "d1", momentum_score=0.40), _snap("LO", "d2", momentum_score=0.01)]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert isinstance(rep, ShadowScoreReport)
    assert _score_of(rep, "HI") > _score_of(rep, "LO")
    assert all(isinstance(t, ShadowTradeScore) for t in rep.trades)
    assert rep.real_orders_placed == 0


def test_high_atr_and_far_below_high_penalize():
    legs = [_leg("CALM", "d1", +5.0), _leg("WILD", "d2", +5.0)]
    snaps = [
        _snap("CALM", "d1", atr_pct=0.02, distance_from_high=-0.01),
        _snap("WILD", "d2", atr_pct=0.20, distance_from_high=-0.40),  # 고변동 + 고점 -40%
    ]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert _score_of(rep, "WILD") < _score_of(rep, "CALM")


def test_missing_fields_penalize_but_safe():
    legs = [_leg("FULL", "d1", +5.0), _leg("PART", "d2", +5.0)]
    full = _snap("FULL", "d1")
    part = _snap("PART", "d2", return_6m=None, relative_strength=None,
                 missing_fields=("return_6m", "relative_strength"))
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag([full, part]))
    # 둘 다 점수는 계산됨(예외 없음), missing 있는 쪽이 더 낮다.
    assert _score_of(rep, "PART") is not None
    assert _score_of(rep, "PART") < _score_of(rep, "FULL")
    part_row = next(t for t in rep.trades if t.symbol == "PART")
    assert part_row.missing_count == 2


# --- 승/패 집계 ---


def test_winners_score_higher_no_warning():
    legs = [
        _leg("W1", "d1", +20.0), _leg("W2", "d2", +10.0),
        _leg("L1", "d3", -15.0), _leg("L2", "d4", -5.0),
    ]
    snaps = [
        _snap("W1", "d1", momentum_score=0.40, relative_strength=0.30, return_3m=0.40),
        _snap("W2", "d2", momentum_score=0.30, relative_strength=0.20, return_3m=0.30),
        _snap("L1", "d3", momentum_score=-0.05, relative_strength=-0.10, return_3m=-0.05),
        _snap("L2", "d4", momentum_score=0.00, relative_strength=-0.02, return_3m=0.01),
    ]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert rep.winner_avg_score > rep.loser_avg_score
    assert rep.separation > 0
    assert not any("분리" in w for w in rep.warnings)


def test_no_separation_emits_warning():
    # 승자가 오히려 약한 피처 → 분리 실패.
    legs = [_leg("W1", "d1", +20.0), _leg("L1", "d2", -15.0)]
    snaps = [
        _snap("W1", "d1", momentum_score=-0.10, relative_strength=-0.10),
        _snap("L1", "d2", momentum_score=0.40, relative_strength=0.30),
    ]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert rep.separation <= 0
    assert any("분리" in w for w in rep.warnings)


def test_correlation_positive_when_score_tracks_pnl():
    legs = [
        _leg("A", "d1", +30.0), _leg("B", "d2", +10.0),
        _leg("C", "d3", -10.0), _leg("D", "d4", -30.0),
    ]
    snaps = [
        _snap("A", "d1", momentum_score=0.40),
        _snap("B", "d2", momentum_score=0.20),
        _snap("C", "d3", momentum_score=0.00),
        _snap("D", "d4", momentum_score=-0.20),
    ]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert rep.score_pnl_correlation is not None
    assert rep.score_pnl_correlation > 0


def test_best_worst_scored():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    snaps = [_snap("A", "d1", momentum_score=0.50), _snap("B", "d2", momentum_score=-0.20)]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    assert rep.best_scored.symbol == "A"
    assert rep.worst_scored.symbol == "B"


# --- fail-safe / 불변 ---


def test_snapshot_none_is_unscored_safe():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    rows = (
        FeatureRow("A", "d1", _snap("A", "d1"), None),
        FeatureRow("B", "d2", None, "가격 데이터 없음"),
    )
    rep = compute_feature_shadow_score(_trade_diag(legs), FeatureDiagnostics(rows=rows))
    assert rep.num_scored == 1
    assert rep.num_unscored == 1
    b = next(t for t in rep.trades if t.symbol == "B")
    assert b.score is None


def test_no_trades_safe():
    rep = compute_feature_shadow_score(_trade_diag([]), _feature_diag([]))
    assert rep.num_scored == 0
    assert rep.winner_avg_score is None
    assert rep.separation is None
    assert rep.real_orders_placed == 0


def test_inputs_not_mutated():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    td = _trade_diag(legs)
    fd = _feature_diag([_snap("A", "d1"), _snap("B", "d2")])
    trades_before, rows_before = td.trades, fd.rows
    compute_feature_shadow_score(td, fd)
    assert td.trades == trades_before
    assert fd.rows == rows_before


def test_format_contains_sections():
    legs = [_leg("A", "d1", +5.0), _leg("B", "d2", -5.0)]
    snaps = [_snap("A", "d1"), _snap("B", "d2", momentum_score=-0.1)]
    rep = compute_feature_shadow_score(_trade_diag(legs), _feature_diag(snaps))
    text = format_feature_shadow_score(rep)
    assert "Shadow Score" in text
    assert "separation" in text.lower()
    assert "real_orders_placed : 0" in text
