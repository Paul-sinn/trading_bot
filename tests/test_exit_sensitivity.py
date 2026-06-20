"""exit_sensitivity 테스트 (spec: specs/exit_sensitivity.md).

청산 그리드(stop×trail×hold) 스윕으로 성과 민감도 점검(실험 러너 — 측정만). 기존 run_sim 로직 그대로,
매매 불변. 데이터 누락 fail-closed. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import exit_sensitivity as es  # noqa: E402


def _fake_result(*, trades, cum, mdd, win, pnl):
    perf = SimpleNamespace(
        num_trades=trades, cumulative_return=cum, max_drawdown=mdd,
        win_rate=win, total_pnl=pnl, num_closed_trades=trades,
    )
    return SimpleNamespace(performance=perf, real_orders_placed=0)


def _fake_robustness(warnings=()):
    return SimpleNamespace(top_symbol=None, top_symbol_pnl_share=None, warnings=tuple(warnings))


# --- 그리드 ---


def test_grid_generation():
    grid = es.generate_grid((0.10, 0.15, 0.20), (0.15, 0.20, 0.25), (45, 60, 90))
    assert len(grid) == 27
    assert len(set(grid)) == 27
    assert (0.15, 0.20, 60) in grid


# --- 집계(주입 simulate_fn) ---


def _uniform_fn(metric_by_combo):
    def _fn(config, stop, trail, hold):
        m = metric_by_combo((stop, trail, hold))
        return _fake_result(**m), _fake_robustness(m.get("warnings", ()))
    # _fake_result는 warnings 인자를 안 받으므로 분리.
    def _fn2(config, stop, trail, hold):
        m = dict(metric_by_combo((stop, trail, hold)))
        warns = m.pop("warnings", ())
        return _fake_result(**m), _fake_robustness(warns)
    return _fn2


def test_run_results_aggregate():
    # (0.15,0.20,60)이 최고 수익. (0.10,0.15,45)가 최저 MDD.
    def metric(combo):
        if combo == (0.15, 0.20, 60):
            return {"trades": 50, "cum": 0.90, "mdd": 0.20, "win": 0.6, "pnl": 900.0}
        if combo == (0.10, 0.15, 45):
            return {"trades": 40, "cum": 0.30, "mdd": 0.05, "win": 0.5, "pnl": 300.0}
        return {"trades": 45, "cum": 0.40, "mdd": 0.15, "win": 0.55, "pnl": 400.0}

    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    assert len(rep.results) == 27
    assert rep.best_by_return.cumulative_return == 0.90
    assert (rep.best_by_return.stop_loss_pct, rep.best_by_return.trailing_stop_pct,
            rep.best_by_return.max_holding_days) == (0.15, 0.20, 60)
    assert rep.safest_by_mdd.max_drawdown == 0.05
    # return/MDD 최고: 0.30/0.05=6.0 (safest) vs 0.90/0.20=4.5 vs 0.40/0.15=2.67
    assert rep.best_by_return_mdd.return_mdd_ratio == max(
        r.return_mdd_ratio for r in rep.results if r.return_mdd_ratio is not None
    )
    assert rep.real_orders_placed == 0
    assert all(r.real_orders_placed == 0 for r in rep.results)


def test_return_mdd_ratio():
    def metric(combo):
        return {"trades": 10, "cum": 0.5, "mdd": 0.1, "win": 0.5, "pnl": 50.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    assert rep.results[0].return_mdd_ratio == 5.0


def test_default_result_located():
    def metric(combo):
        return {"trades": 10, "cum": 0.3, "mdd": 0.1, "win": 0.5, "pnl": 30.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    d = rep.default_result
    assert (d.stop_loss_pct, d.trailing_stop_pct, d.max_holding_days) == (0.15, 0.20, 60)


# --- 경고 ---


def test_narrow_setting_warning():
    # 한 조합만 압도적 → 단일 설정 의존 경고.
    def metric(combo):
        if combo == (0.20, 0.25, 90):
            return {"trades": 50, "cum": 2.0, "mdd": 0.2, "win": 0.6, "pnl": 2000.0}
        return {"trades": 40, "cum": 0.10, "mdd": 0.1, "win": 0.5, "pnl": 100.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    assert any("단일" in w or "좁" in w for w in rep.warnings)


def test_collapse_warning_on_high_spread():
    def metric(combo):
        if combo == (0.10, 0.15, 45):
            return {"trades": 30, "cum": -0.20, "mdd": 0.3, "win": 0.3, "pnl": -200.0}
        return {"trades": 45, "cum": 0.80, "mdd": 0.15, "win": 0.6, "pnl": 800.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    assert any("민감" in w or "붕괴" in w for w in rep.warnings)


def test_robust_grid_no_fragility_warning():
    def metric(combo):
        return {"trades": 45, "cum": 0.50, "mdd": 0.12, "win": 0.58, "pnl": 500.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    assert not any("단일" in w or "민감" in w or "붕괴" in w for w in rep.warnings)


# --- fail-closed ---


def test_all_fail_when_data_missing():
    def _boom(config, stop, trail, hold):
        from run_sim import DataAdapterError
        raise DataAdapterError("데이터 폴더 없음")
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="nope"), simulate_fn=_boom)
    assert all(r.error is not None for r in rep.results)
    assert rep.best_by_return is None
    assert any("실패" in w or "데이터" in w for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_real_missing_folder_fail_closed():
    rep = es.run_sensitivity(es.ExitGridConfig(
        data_root="does_not_exist_zzz", symbols=("SPY", "NVDA"),
        assume_no_events=True, events_csv=None,
    ))
    assert all(r.error is not None for r in rep.results)
    assert rep.real_orders_placed == 0


def test_format_contains_sections():
    def metric(combo):
        return {"trades": 10, "cum": 0.3, "mdd": 0.1, "win": 0.5, "pnl": 30.0}
    rep = es.run_sensitivity(es.ExitGridConfig(data_root="x"), simulate_fn=_uniform_fn(metric))
    text = es.format_exit_sensitivity(rep)
    for token in ("Exit", "stop", "trail", "hold", "ret/MDD", "real_orders_placed"):
        assert token.lower() in text.lower(), token
