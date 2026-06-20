"""다일 성과 리포트 테스트 (spec: specs/perf_report.md).

스냅샷 + 매매로그에서 누적수익·MDD·승률·equity 곡선 등 산출. 빈 케이스 안전. real orders=0.
측정만 — 전략/상태 미변경. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.base import AgentRegistry
from agents.multiday import DayInput, run_phase1_multiday
from agents.perf_report import (
    PerformanceReport,
    compute_performance,
    format_performance_report,
    performance_from_multiday,
)
from agents.phase1_flow import CandidateContext
from agents.policy_loader import load_policy
from agents.scanner import MockPriceDataProvider, ScannerAgent
from agents.sim_exit import ExitParams
from agents.sim_portfolio import PortfolioSnapshot, TradeRecord
from algorithms.regime import Regime

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _snap(equity, *, exposure=0.0, unrealized=0.0, realized=0.0, starting=100_000.0):
    return PortfolioSnapshot(
        starting_cash=starting, cash=equity - exposure, total_exposure=exposure, equity=equity,
        realized_pnl=realized, open_positions=0, open_symbols=(), trade_count=0,
        market_value=exposure, unrealized_pnl=unrealized,
    )


def _sell(realized):
    return TradeRecord("X", "sell", 1, 100.0, 100.0, 0.0, realized)


def _buy():
    return TradeRecord("X", "buy", 1, 100.0, 100.0, 0.0, 0.0)


# --- 순수 계산 ---


def test_cumulative_return_calculation():
    snaps = (_snap(100_000.0), _snap(101_000.0), _snap(110_000.0))
    rep = compute_performance(snaps, (), starting_cash=100_000.0)
    assert rep.cumulative_return == pytest.approx(0.10)
    assert rep.equity_curve == (100_000.0, 101_000.0, 110_000.0)


def test_max_drawdown_calculation():
    # 100 → 120(peak) → 90(trough) → 110. MDD = (120-90)/120 = 0.25.
    snaps = tuple(_snap(e) for e in (100.0, 120.0, 90.0, 110.0))
    rep = compute_performance(snaps, (), starting_cash=100.0)
    assert rep.max_drawdown == pytest.approx(0.25)


def test_win_rate_and_avg_win_loss():
    trades = (_buy(), _sell(120.0), _sell(80.0), _sell(-50.0))  # 2승 1패
    rep = compute_performance((_snap(100_000.0),), trades, starting_cash=100_000.0)
    assert rep.num_trades == 4
    assert rep.num_closed_trades == 3
    assert rep.win_rate == pytest.approx(2 / 3)
    assert rep.average_win == pytest.approx((120.0 + 80.0) / 2)
    assert rep.average_loss == pytest.approx(-50.0)
    assert rep.realized_pnl == pytest.approx(120.0 + 80.0 - 50.0)


def test_equity_curve_and_exposure_from_snapshots():
    snaps = (_snap(100_000.0, exposure=0.0), _snap(101_000.0, exposure=2000.0, unrealized=200.0))
    rep = compute_performance(snaps, (), starting_cash=100_000.0)
    assert rep.equity_curve == (100_000.0, 101_000.0)
    assert rep.exposure_over_time == (0.0, 2000.0)
    assert rep.unrealized_pnl == pytest.approx(200.0)   # 최종 스냅샷
    assert rep.total_pnl == pytest.approx(rep.realized_pnl + rep.unrealized_pnl)


def test_empty_no_trade_case_safe():
    rep = compute_performance((), (), starting_cash=100_000.0)
    assert rep.equity_curve == () and rep.exposure_over_time == ()
    assert rep.cumulative_return == 0.0
    assert rep.max_drawdown == 0.0
    assert rep.realized_pnl == 0.0 and rep.unrealized_pnl == 0.0 and rep.total_pnl == 0.0
    assert rep.win_rate == 0.0 and rep.average_win == 0.0 and rep.average_loss == 0.0
    assert rep.num_trades == 0 and rep.num_closed_trades == 0
    assert rep.real_orders_placed == 0


def test_zero_starting_cash_no_divide_error():
    rep = compute_performance((_snap(0.0, starting=0.0),), (), starting_cash=0.0)
    assert rep.cumulative_return == 0.0


def test_breakeven_sell_not_win_or_loss():
    rep = compute_performance((_snap(100_000.0),), (_sell(0.0),), starting_cash=100_000.0)
    assert rep.num_closed_trades == 1
    assert rep.win_rate == 0.0          # 0 실현은 승 아님
    assert rep.average_win == 0.0 and rep.average_loss == 0.0


def test_report_is_frozen_and_formattable():
    rep = compute_performance((_snap(100_000.0),), (), starting_cash=100_000.0)
    assert isinstance(rep, PerformanceReport)
    text = format_performance_report(rep)
    assert "cumulative_return" in text and "max_drawdown" in text
    with pytest.raises(Exception):
        rep.win_rate = 1.0  # type: ignore[misc]


# --- multiday 통합 ---


def _bullish_df():
    prices = np.linspace(80, 200, 260)
    vol = np.full(len(prices), 1_000_000.0)
    vol[-1] = 5_000_000.0
    return pd.DataFrame(
        {"open": prices, "high": prices * 1.005, "low": prices * 0.995,
         "close": prices, "volume": vol}
    )


def _scanner(symbols):
    return ScannerAgent(
        AgentRegistry(), MockPriceDataProvider({s: _bullish_df() for s in symbols}), list(symbols)
    )


def _ctx(quantity=10, reference_price=100.0, **ov):
    base = dict(
        stop_loss_pct=0.05, per_trade_risk_pct=0.04, regime=Regime.NORMAL_BULL,
        quantity=quantity, reference_price=reference_price,
        trend_confirmed=True, volume_confirmed=True, relative_strength_confirmed=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return CandidateContext(**base)


def test_performance_from_multiday():
    # d1 매수, d2 가격↑(미실현↑), d3 익절 청산.
    days = [
        DayInput("d1", _scanner(["NVDA"]), {"NVDA": _ctx(10, 100.0)}, mark_prices={"NVDA": 100.0}),
        DayInput("d2", _scanner([]), {}, mark_prices={"NVDA": 130.0}),
        DayInput("d3", _scanner([]), {}, mark_prices={"NVDA": 130.0},
                 exits={"NVDA": ExitParams(manual_exit=True)}),
    ]
    res = asyncio.run(run_phase1_multiday(days=days, policy=load_policy(REAL_CONFIG), account_cash=100_000.0))
    rep = performance_from_multiday(res)
    assert len(rep.equity_curve) == 3
    assert rep.num_closed_trades == 1
    assert rep.realized_pnl > 0                 # 130 > 매수가 → 익절
    assert rep.win_rate == pytest.approx(1.0)
    assert rep.real_orders_placed == 0
