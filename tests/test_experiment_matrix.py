"""experiment_matrix 테스트 (spec: specs/experiment_matrix.md).

여러 유니버스/설정 실험을 기존 run_sim 로직으로 돌려 비교(실험 러너 — 측정만). 매매/veto 불변, 누락
입력 fail-safe. real_orders=0. 네트워크 없음.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import experiment_matrix as em  # noqa: E402


def _fake_result(*, trades, cum, mdd, win, pnl):
    perf = SimpleNamespace(
        num_trades=trades, cumulative_return=cum, max_drawdown=mdd,
        win_rate=win, total_pnl=pnl, num_closed_trades=trades,
    )
    return SimpleNamespace(performance=perf, real_orders_placed=0)


def _fake_robustness(top_symbol, share, warnings=()):
    return SimpleNamespace(
        top_symbol=top_symbol, top_symbol_pnl_share=share, warnings=tuple(warnings),
    )


def _fake_simulate_factory(table):
    """config.name → (result, robustness, count) 매핑으로 simulate_fn을 만든다."""
    def _fn(config):
        return table[config.name]
    return _fn


# --- 비교(주입 simulate_fn) ---


def test_matrix_compares_two_universes():
    table = {
        "small": (_fake_result(trades=48, cum=0.2625, mdd=0.0714, win=0.5556, pnl=262.49),
                  _fake_robustness("AMD", 0.67, ("AMD 집중", "AMD 붕괴")), 6),
        "expanded": (_fake_result(trades=104, cum=0.9846, mdd=0.1390, win=0.5652, pnl=984.57),
                     _fake_robustness("MU", 0.24, ()), 20),
    }
    configs = [
        em.ExperimentConfig(name="small", data_root="x", symbols=("SPY", "NVDA", "AAPL", "MSFT", "AMD", "GOOGL")),
        em.ExperimentConfig(name="expanded", data_root="y", symbols=tuple("ABCDEFGHIJKLMNOPQRST")),
    ]
    rep = em.run_matrix(configs, simulate_fn=_fake_simulate_factory(table))
    assert isinstance(rep, em.MatrixReport)
    assert len(rep.results) == 2
    by = {r.name: r for r in rep.results}
    assert by["small"].symbols_count == 6
    assert by["small"].trades == 48
    assert by["small"].top_symbol == "AMD"
    assert by["small"].top_symbol_pnl_share == pytest.approx(0.67)
    assert len(by["small"].robustness_warnings) == 2
    assert by["expanded"].symbols_count == 20
    assert by["expanded"].cumulative_return == pytest.approx(0.9846)
    assert by["expanded"].robustness_warnings == ()
    assert by["small"].error is None
    assert rep.real_orders_placed == 0
    assert all(r.real_orders_placed == 0 for r in rep.results)


def test_report_includes_key_metrics():
    table = {"e": (_fake_result(trades=10, cum=0.5, mdd=0.1, win=0.6, pnl=500.0),
                   _fake_robustness("AMD", 0.4, ("warn1",)), 6)}
    rep = em.run_matrix(
        [em.ExperimentConfig(name="e", data_root="x", symbols=("SPY", "NVDA"))],
        simulate_fn=_fake_simulate_factory(table),
    )
    text = em.format_matrix(rep)
    for token in ("experiment", "trades", "cum", "MDD", "win", "PnL", "top", "warn", "real_orders_placed"):
        assert token.lower() in text.lower(), token
    assert "AMD" in text


def test_top_share_and_warnings_surfaced():
    table = {"e": (_fake_result(trades=5, cum=0.1, mdd=0.05, win=0.5, pnl=50.0),
                   _fake_robustness("NVDA", 0.8, ("NVDA 집중",)), 6)}
    r = em.run_experiment(
        em.ExperimentConfig(name="e", data_root="x"),
        simulate_fn=_fake_simulate_factory(table),
    )
    assert r.top_symbol == "NVDA"
    assert r.top_symbol_pnl_share == pytest.approx(0.8)
    assert r.robustness_warnings == ("NVDA 집중",)


# --- fail-safe ---


def test_missing_input_fails_safely():
    # 한 실험은 실패(데이터 폴더 없음), 다른 실험은 성공 → 매트릭스는 계속.
    ok = {"good": (_fake_result(trades=3, cum=0.1, mdd=0.02, win=0.66, pnl=30.0),
                   _fake_robustness("A", 0.3, ()), 2)}

    def _mixed(config):
        if config.name == "bad":
            from run_sim import DataAdapterError
            raise DataAdapterError("데이터 폴더 없음: nope")
        return ok[config.name]

    rep = em.run_matrix(
        [em.ExperimentConfig(name="bad", data_root="nope", symbols=("SPY", "X")),
         em.ExperimentConfig(name="good", data_root="x")],
        simulate_fn=_mixed,
    )
    by = {r.name: r for r in rep.results}
    assert by["bad"].error is not None
    assert by["bad"].trades == 0
    assert by["bad"].cumulative_return is None     # 가짜 메트릭 금지
    assert by["bad"].real_orders_placed == 0
    assert by["good"].error is None                # 나머지는 계속
    assert by["good"].trades == 3
    text = em.format_matrix(rep)
    assert "bad" in text and "good" in text


def test_real_orders_zero_on_all_results():
    table = {"e": (_fake_result(trades=1, cum=0.0, mdd=0.0, win=0.0, pnl=0.0),
                   _fake_robustness(None, None, ()), 1)}
    rep = em.run_matrix(
        [em.ExperimentConfig(name="e", data_root="x")],
        simulate_fn=_fake_simulate_factory(table),
    )
    assert rep.real_orders_placed == 0
    assert rep.results[0].real_orders_placed == 0


# --- 실 fixture 폴더 통합 (_default_simulate) ---


def _write_symbol(folder, symbol, close, volume):
    close = np.asarray(close, dtype=float)
    df = pd.DataFrame({
        "symbol": symbol,
        "date": pd.date_range("2024-01-01", periods=len(close), freq="B").strftime("%Y-%m-%d"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.asarray(volume, dtype=float),
    })
    df.to_csv(folder / f"{symbol}.csv", index=False)


def _build_fixture_folder(folder):
    folder.mkdir(parents=True, exist_ok=True)
    n = 260
    spike = np.full(n, 1_000_000.0)
    spike[-40:] = 6_000_000.0
    flat = np.full(n, 1_000_000.0)
    _write_symbol(folder, "SPY", np.linspace(300, 400, n), flat)
    _write_symbol(folder, "NVDA", np.linspace(80, 200, n), spike)
    _write_symbol(folder, "AAPL", np.linspace(90, 240, n), spike)


def test_real_fixture_integration_no_behavior_change(tmp_path):
    folder = tmp_path / "uni"
    _build_fixture_folder(folder)
    config = em.ExperimentConfig(
        name="fixture", data_root=str(folder), benchmark="SPY",
        symbols=("SPY", "NVDA", "AAPL"), warmup=150,
        assume_no_events=True, events_csv=None,   # 명시적 바이패스
    )
    r = em.run_experiment(config)
    assert r.error is None
    assert r.symbols_count == 3
    assert r.trades >= 0
    assert r.cumulative_return is not None
    assert r.real_orders_placed == 0


def test_real_missing_folder_fails_safely():
    config = em.ExperimentConfig(
        name="missing", data_root="does_not_exist_zzz", symbols=("SPY", "NVDA"),
        assume_no_events=True, events_csv=None,
    )
    r = em.run_experiment(config)
    assert r.error is not None
    assert r.real_orders_placed == 0
