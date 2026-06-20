"""robustness_report 테스트 (spec: specs/robustness_report.md).

성과가 심볼·기간에 강건한지 점검(측정 전용). 분기 윈도우/심볼별/집중·의존 경고/LOO. 입력 불변,
작은 표본 안전. real_orders=0. 네트워크 없음.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from agents.robustness_report import (
    RobustnessReport,
    compute_robustness_report,
    format_robustness_report,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _leg(symbol, pnl, *, entry="2025-01-15", exit_="2025-02-15"):
    return TradeLeg(
        symbol=symbol, entry_date=entry, exit_date=exit_,
        entry_price=100.0, exit_price=100.0 + pnl, qty=1.0,
        pnl=pnl, pnl_pct=pnl / 100.0, exit_reason="sell",
    )


def _trade_diag(legs, equity=()):
    return TradeDiagnostics(
        trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
        equity_over_time=tuple(equity), exposure_over_time=(), top_symbols_by_pnl=(),
        top_veto_reasons=(),
    )


def _report(legs, equity=(), **kw):
    return compute_robustness_report(None, None, trade_diag=_trade_diag(legs, equity), **kw)


# --- 윈도우 ---


def test_window_stats_compute():
    equity = [
        ("2025-01-31", 1000.0), ("2025-02-28", 1100.0), ("2025-03-31", 1050.0),  # Q1
        ("2025-04-30", 1050.0), ("2025-05-31", 1300.0), ("2025-06-30", 1200.0),  # Q2
    ]
    rep = _report([_leg("A", 50.0)], equity)
    assert isinstance(rep, RobustnessReport)
    q = {w.label: w for w in rep.windows}
    assert set(q) == {"2025-Q1", "2025-Q2"}
    assert q["2025-Q1"].return_pct == pytest.approx(0.05)        # 1050/1000-1
    assert q["2025-Q2"].return_pct == pytest.approx(1200 / 1050 - 1)
    assert q["2025-Q1"].max_drawdown == pytest.approx((1100 - 1050) / 1100)
    assert rep.best_window.label == "2025-Q2"
    assert rep.worst_window.label == "2025-Q1"
    assert rep.real_orders_placed == 0


# --- 심볼별 / 집중 ---


def _concentrated():
    return [
        _leg("AMD", 100.0), _leg("AMD", 80.0),
        _leg("MSFT", 5.0), _leg("MSFT", -2.0),
        _leg("NVDA", 1.0), _leg("NVDA", -1.0),
    ]


def test_symbol_performance_and_concentration():
    rep = _report(_concentrated())
    by = {s.symbol: s for s in rep.symbol_perf}
    assert by["AMD"].total_pnl == 180.0
    assert by["AMD"].win_rate == 1.0
    assert by["MSFT"].win_rate == 0.5
    assert rep.top_symbol == "AMD"
    assert rep.top_symbol_pnl_share == pytest.approx(180.0 / 183.0)
    assert rep.actual_total_pnl == 183.0


def test_one_symbol_dependency_warning():
    rep = _report(_concentrated())
    assert any("AMD" in w and ("집중" in w or "%" in w) for w in rep.warnings)


def test_collapse_warning_on_top_removal():
    rep = _report(_concentrated())
    amd_loo = next(l for l in rep.leave_one_out if l.excluded_symbol == "AMD")
    assert amd_loo.total_pnl == pytest.approx(3.0)          # 183 - 180
    assert amd_loo.total_pnl_diff == pytest.approx(-180.0)
    assert any("붕괴" in w or "의존" in w for w in rep.warnings)


def test_balanced_book_no_dependency_warning():
    legs = [_leg("A", 30.0), _leg("B", 28.0), _leg("C", 26.0), _leg("D", 24.0)]
    rep = _report(legs)
    assert rep.top_symbol_pnl_share < 0.5
    assert not any("집중" in w or "붕괴" in w for w in rep.warnings)


# --- LOO 재시뮬 경로 ---


def test_leave_one_out_with_rerun_results():
    rerun = {
        "AMD": SimpleNamespace(performance=SimpleNamespace(
            total_pnl=3.0, cumulative_return=0.003, max_drawdown=0.07))
    }
    rep = _report(_concentrated(), rerun_results=rerun)
    amd = next(l for l in rep.leave_one_out if l.excluded_symbol == "AMD")
    assert amd.mode == "rerun"
    assert amd.total_pnl == pytest.approx(3.0)
    assert amd.return_pct == pytest.approx(0.003)
    assert amd.max_drawdown == pytest.approx(0.07)
    msft = next(l for l in rep.leave_one_out if l.excluded_symbol == "MSFT")
    assert msft.mode == "trade-removal"        # rerun 없는 심볼은 근사


# --- fail-safe / 불변 ---


def test_small_sample_safe():
    rep = _report([_leg("A", 5.0)])
    assert any("부족" in w for w in rep.warnings)
    assert rep.real_orders_placed == 0


def test_no_trades_safe():
    rep = _report([])
    assert rep.actual_total_pnl == 0.0
    assert rep.symbol_perf == ()
    assert rep.top_symbol is None
    assert rep.windows == ()


def test_inputs_not_mutated():
    legs = _concentrated()
    td = _trade_diag(legs, [("2025-01-31", 1000.0)])
    trades_before = td.trades
    equity_before = td.equity_over_time
    compute_robustness_report(None, None, trade_diag=td)
    assert td.trades == trades_before
    assert td.equity_over_time == equity_before


def test_format_contains_sections():
    rep = _report(_concentrated(), equity=[("2025-01-31", 1000.0), ("2025-03-31", 1100.0)])
    text = format_robustness_report(rep)
    assert "Robustness" in text
    assert "AMD" in text
    assert "real_orders_placed : 0" in text


# --- 실 historical_sim 통합: 매매/veto 불변 ---


def test_real_sim_trades_unchanged():
    dates = pd.date_range("2024-01-01", periods=260, freq="B")
    close = np.linspace(80, 200, 260)
    vol = np.full(260, 1_000_000.0)
    vol[-3:] = 6_000_000.0
    frame = pd.concat([
        pd.DataFrame({"symbol": "NVDA", "date": dates, "open": close, "high": close * 1.01,
                      "low": close * 0.99, "close": close, "volume": vol}),
        pd.DataFrame({"symbol": "SPY", "date": dates, "open": np.linspace(300, 400, 260),
                      "high": np.linspace(300, 400, 260) * 1.01, "low": np.linspace(300, 400, 260) * 0.99,
                      "close": np.linspace(300, 400, 260), "volume": np.full(260, 1e6)}),
        pd.DataFrame({"symbol": "BENCH", "date": dates, "open": np.linspace(100, 110, 260),
                      "high": np.linspace(100, 110, 260) * 1.01, "low": np.linspace(100, 110, 260) * 0.99,
                      "close": np.linspace(100, 110, 260), "volume": np.full(260, 1e6)}),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    res = asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=list(spy.index[-3:]),
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=MockEventRiskProvider(default=True),
    ))
    trade_log_before = tuple(res.multiday.portfolio.trade_log)
    rep = compute_robustness_report(res.multiday, {"NVDA": data["NVDA"]})
    assert tuple(res.multiday.portfolio.trade_log) == trade_log_before
    assert rep.real_orders_placed == 0
    assert res.real_orders_placed == 0
