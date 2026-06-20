"""shadow_whatif 테스트 (spec: specs/shadow_whatif.md).

저점수 트레이드를 걸렀다면? 고정 필터로 성과 차이를 추정(측정 전용). 실 매매/입력 불변, 단일심볼 집중
경고, 작은 표본 안전. real_orders=0. 네트워크 없음.
"""

from types import SimpleNamespace

from agents.feature_shadow_score import ShadowScoreReport, ShadowTradeScore
from agents.shadow_whatif import (
    FilterScenario,
    ShadowWhatIfReport,
    compute_shadow_whatif,
    format_shadow_whatif,
)
from agents.trade_diagnostics import TradeDiagnostics


def _ts(symbol, score, pnl, date="2025-01-01"):
    return ShadowTradeScore(
        symbol=symbol, entry_date=date, score=score, pnl=pnl,
        is_winner=(pnl > 0 if pnl is not None else None), missing_count=0,
    )


def _shadow(scores):
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


def _scn(report, name):
    return next(s for s in report.scenarios if s.name == name)


def _eight():
    # 점수 1..8, pnl이 점수와 같은 방향(상위가 승리).
    return [
        _ts("A", 1.0, -10.0), _ts("B", 2.0, -4.0), _ts("C", 3.0, -1.0), _ts("D", 4.0, 1.0),
        _ts("E", 5.0, 2.0), _ts("F", 6.0, 4.0), _ts("G", 7.0, 8.0), _ts("H", 8.0, 12.0),
    ]


# --- 필터 계산 ---


def test_actual_baseline():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(_eight()))
    assert isinstance(rep, ShadowWhatIfReport)
    assert rep.actual.kept_count == 8
    assert rep.actual.removed_count == 0
    assert rep.actual.total_pnl == 12.0       # 합
    assert rep.real_orders_placed == 0


def test_keep_top_quartile():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(_eight()))
    s = _scn(rep, "keep-top-quartile")
    assert s.kept_count == 2                   # 상위 25% (G,H)
    assert set(s.symbols_kept) == {"G", "H"}
    assert s.total_pnl == 20.0                 # 8+12
    assert s.win_rate == 1.0
    assert s.total_pnl_diff == 8.0             # 20 - 12 (actual)


def test_keep_top_half_and_drop_bottom_quartile():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(_eight()))
    half = _scn(rep, "keep-top-half")
    assert half.kept_count == 4                # E,F,G,H
    drop_bottom = _scn(rep, "drop-bottom-quartile")
    assert drop_bottom.kept_count == 6         # C..H
    assert set(drop_bottom.symbols_removed) == {"A", "B"}


def test_drop_negative_scores():
    scores = [_ts("A", -1.0, -5.0), _ts("B", -0.5, -2.0), _ts("C", 1.0, 3.0), _ts("D", 2.0, 6.0)]
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(scores))
    s = _scn(rep, "drop-negative-scores")
    assert s.kept_count == 2                    # C,D (양수 점수)
    assert set(s.symbols_removed) == {"A", "B"}
    assert s.total_pnl == 9.0


def test_drop_negative_scores_noop_when_none():
    scores = [_ts("A", 1.0, 3.0), _ts("B", 2.0, 6.0)]
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(scores))
    s = _scn(rep, "drop-negative-scores")
    assert s.removed_count == 0                 # 음수 점수 없음 → actual과 동일(안전)


# --- leave-one-out / 집중 경고 ---


def test_leave_one_out_amd_dependence_warning():
    # AMD가 총손익을 좌우 — AMD 제거 시 성과 급감.
    scores = [
        _ts("AMD", 8.0, 100.0), _ts("AMD", 7.0, 80.0),
        _ts("MSFT", 4.0, 3.0), _ts("MSFT", 3.0, -2.0),
        _ts("NVDA", 2.0, 1.0), _ts("NVDA", 1.0, -1.0),
    ]
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(scores))
    drop_amd = _scn(rep, "drop-AMD")
    assert "AMD" in drop_amd.symbols_removed
    assert drop_amd.total_pnl_diff < 0                  # AMD 빼면 총손익 급감
    assert any("AMD" in w for w in rep.warnings)        # 단일심볼 의존 경고


def test_concentration_warning_on_kept_set():
    scores = [
        _ts("AMD", 8.0, 90.0), _ts("MSFT", 7.0, 5.0), _ts("NVDA", 6.0, 5.0),
        _ts("A", 1.0, -3.0),
    ]
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(scores))
    # 상위 유지 집합의 손익 대부분이 AMD → concentration_warning.
    top_half = _scn(rep, "keep-top-half")
    assert top_half.top_symbol == "AMD"
    assert top_half.top_symbol_pnl_share > 0.6
    assert top_half.concentration_warning is not None


def test_mdd_proxy_computed():
    # 진입일순 누적손익이 한번 꺾임 → mdd_proxy > 0.
    scores = [
        _ts("A", 1.0, 10.0, "2025-01-01"),
        _ts("B", 2.0, -8.0, "2025-01-02"),
        _ts("C", 3.0, 5.0, "2025-01-03"),
    ]
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(scores))
    assert rep.actual.mdd_proxy == 8.0          # 10 고점 → 2로, 낙폭 8


# --- fail-safe / 불변 ---


def test_small_sample_safe():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow([_ts("A", 1.0, 5.0)]))
    assert rep.actual.kept_count == 1
    s = _scn(rep, "keep-top-quartile")
    assert s.kept_count >= 0                     # 예외 없음
    assert rep.real_orders_placed == 0


def test_no_trades_safe():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow([]))
    assert rep.actual.kept_count == 0
    assert rep.actual.total_pnl == 0.0
    assert rep.actual.mdd_proxy is None


def test_inputs_not_mutated():
    scores = _eight()
    sr = _shadow(scores)
    td = _empty_trade_diag()
    before = sr.trades
    compute_shadow_whatif(td, sr)
    assert sr.trades == before
    assert td.trades == ()


def test_format_contains_sections():
    rep = compute_shadow_whatif(_empty_trade_diag(), _shadow(_eight()))
    text = format_shadow_whatif(rep)
    assert "What-if" in text
    assert "keep-top-quartile" in text
    assert "real_orders_placed : 0" in text


def test_duck_typed_report_accepted():
    rep = compute_shadow_whatif(
        _empty_trade_diag(), SimpleNamespace(trades=tuple(_eight()))
    )
    assert rep.actual.kept_count == 8
    assert all(isinstance(s, FilterScenario) for s in rep.scenarios)
