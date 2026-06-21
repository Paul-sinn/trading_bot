"""trend_leverage_experiment 테스트 (spec: specs/trend_leverage_experiment.md).

변형 러너 + winner_extension(report-only, 손실 제외) + 레버리지 데이터 없음 안전 skip. real_orders=0.
네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import trend_leverage_experiment as tle  # noqa: E402
from agents.trade_diagnostics import TradeLeg  # noqa: E402


def _perf(cum, mdd, win, pnl, trades):
    return SimpleNamespace(cumulative_return=cum, max_drawdown=mdd, win_rate=win,
                           total_pnl=pnl, num_trades=trades, num_closed_trades=trades)


def _leg(symbol, *, entry, exit_, pnl, reason):
    return TradeLeg(symbol=symbol, entry_date=entry, exit_date=exit_, entry_price=100.0,
                    exit_price=100.0 + pnl, qty=1.0, pnl=pnl, pnl_pct=pnl / 100.0, exit_reason=reason)


# --- 메트릭 ---


def test_variant_metrics_holding_and_exits():
    legs = [
        _leg("AMD", entry="2025-01-02", exit_="2025-03-03", pnl=50.0, reason="time_stop"),
        _leg("NVDA", entry="2025-01-02", exit_="2025-01-20", pnl=-10.0, reason="stop_loss_hit"),
    ]
    v = tle._variant_metrics("x", _perf(0.4, 0.1, 0.5, 40.0, 2), legs, "2025-04-01")
    assert v.trades == 2
    assert v.avg_holding_days is not None and v.longest_holding_days is not None
    assert dict(v.exit_reason_dist) == {"time_stop": 1, "stop_loss_hit": 1}
    assert v.return_mdd_ratio == 4.0           # 0.4/0.1
    assert v.top_symbol == "AMD"
    assert v.real_orders_placed == 0


def test_weekend_exit_count_in_metrics():
    legs = [_leg("TQQQ", entry="2025-01-02", exit_="2025-01-10", pnl=5.0, reason="weekend_exit")]
    v = tle._variant_metrics("lev", _perf(0.1, 0.05, 1.0, 5.0, 1), legs, "2025-02-01")
    assert v.weekend_exit_count == 1


# --- winner_extension: 손실 포지션 제외 ---


def test_extension_candidates_exclude_losers():
    legs = [
        _leg("A", entry="2025-01-02", exit_="2025-03-03", pnl=30.0, reason="time_stop"),   # 수익 → 후보
        _leg("B", entry="2025-01-02", exit_="2025-03-03", pnl=-5.0, reason="time_stop"),   # 손실 → 제외
        _leg("C", entry="2025-01-02", exit_="2025-02-02", pnl=20.0, reason="trailing_stop_hit"),  # time_stop 아님 → 제외
    ]
    cands = tle.compute_extension_candidates(legs)
    syms = {l.symbol for l in cands}
    assert syms == {"A"}                       # 수익 time_stop만
    assert all(l.pnl > 0 for l in cands)


# --- 변형 러너 (주입 simulate_fn) ---


def _fake_result(perf):
    md = SimpleNamespace(
        day_results=(), portfolio=SimpleNamespace(trade_log=(), positions={}),
    )
    return SimpleNamespace(performance=perf, multiday=md, real_orders_placed=0,
                           portfolio=md.portfolio)


def test_run_variant_via_fake_simulate(monkeypatch):
    def _fake(args):
        return _fake_result(_perf(0.3, 0.1, 0.6, 300.0, 10))
    monkeypatch.setattr(tle.run_sim, "_final_marks", lambda a, r: {})
    v = tle.run_variant(tle.VariantConfig(name="b", data_root="x"), simulate_fn=_fake)
    assert v.name == "b"
    assert v.cumulative_return == 0.3
    assert v.real_orders_placed == 0


def test_experiment_runs_three_variants_and_extension(monkeypatch):
    monkeypatch.setattr(tle.run_sim, "_final_marks", lambda a, r: {})

    def _fake(args):
        # max_holding이 클수록 총손익 약간 증가(연장 효과 모사).
        pnl = {60: 260.0, 90: 280.0, 120: 290.0}.get(args.max_holding_days, 260.0)
        return _fake_result(_perf(0.26, 0.07, 0.55, pnl, 48))

    rep = tle.run_trend_leverage_experiment(universe_root="x", simulate_fn=_fake)
    assert [v.name for v in rep.variants] == ["baseline_realistic", "trend_extended_90", "trend_extended_120"]
    assert rep.extension is not None
    assert rep.extension.delta_total_pnl_90 == 20.0       # 280 - 260
    assert rep.extension.delta_total_pnl_120 == 30.0
    assert rep.leveraged is None                          # leveraged_root 미지정
    assert rep.real_orders_placed == 0


def test_leveraged_missing_data_skips_safely(monkeypatch):
    monkeypatch.setattr(tle.run_sim, "_final_marks", lambda a, r: {})

    def _fake(args):
        if args.data_root == "no_lev":
            raise tle.run_sim.DataAdapterError("폴더 없음: no_lev")
        return _fake_result(_perf(0.26, 0.07, 0.55, 260.0, 48))

    rep = tle.run_trend_leverage_experiment(
        universe_root="x", leveraged_root="no_lev", simulate_fn=_fake)
    assert rep.leveraged is not None
    assert rep.leveraged.error is not None
    assert any("레버리지" in w or "skip" in w.lower() for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_format_contains_sections(monkeypatch):
    monkeypatch.setattr(tle.run_sim, "_final_marks", lambda a, r: {})

    def _fake(args):
        return _fake_result(_perf(0.26, 0.07, 0.55, 260.0, 48))

    rep = tle.run_trend_leverage_experiment(universe_root="x", simulate_fn=_fake)
    text = tle.format_experiment(rep)
    assert "Trend Extension" in text
    assert "winner_extension" in text
    assert "real_orders_placed : 0" in text
