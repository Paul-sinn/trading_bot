"""trade_diagnostics 테스트 (spec: specs/trade_diagnostics.md).

fixture 매매로그 + 일별 스냅샷/decision에서 매매 단위 진단 산출. 청산/미청산, drawdown 기간,
veto 집계, 진입 증거. real_orders_placed=0. 측정 전용 — 동작 변경 없음. 네트워크/브로커 없음.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from agents.sim_portfolio import PortfolioSnapshot, TradeRecord
from agents.trade_diagnostics import (
    TradeDiagnostics,
    compute_trade_diagnostics,
    format_trade_diagnostics,
)

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


# --- fixture 헬퍼 (덕타이핑 stand-in) ---


def _snap(trade_count, equity, exposure):
    return PortfolioSnapshot(
        starting_cash=1000.0, cash=equity - exposure, total_exposure=exposure,
        equity=equity, realized_pnl=0.0, open_positions=0, open_symbols=(),
        trade_count=trade_count, market_value=exposure,
    )


def _dec(symbol, *, tier="1", weight=0.5, acct_loss=0.04, rationale="ok", passed=True, reasons=()):
    veto = SimpleNamespace(passed=passed, reasons=tuple(reasons))
    return SimpleNamespace(
        symbol=symbol, tier=tier, position_weight=weight, account_loss_pct=acct_loss,
        rationale=rationale, veto=veto,
    )


def _day(date, snap, decisions):
    report = SimpleNamespace(report_date=date, portfolio_snapshot=snap, decisions=tuple(decisions))
    return SimpleNamespace(report=report)


def _buy(symbol, shares, price):
    return TradeRecord(symbol, "buy", shares, price, shares * price, 0.0, 0.0)


def _sell(symbol, shares, price, pnl, reason):
    return TradeRecord(symbol, "sell", shares, price, shares * price, 0.0, pnl, exit_reason=reason)


def _multiday(day_results, trade_log):
    return SimpleNamespace(
        day_results=tuple(day_results),
        portfolio=SimpleNamespace(trade_log=tuple(trade_log)),
    )


# --- 청산 매매: pnl/best/worst ---


def test_closed_trades_pnl_and_best_worst():
    # day1: NVDA 매수 10@100, AAPL 매수 5@200. day2: NVDA 매도 10@120(+200), AAPL 매도 5@180(-100).
    trade_log = [
        _buy("NVDA", 10, 100.0), _buy("AAPL", 5, 200.0),
        _sell("NVDA", 10, 120.0, 200.0, "trailing_stop_hit"),
        _sell("AAPL", 5, 180.0, -100.0, "stop_loss_hit"),
    ]
    days = [
        _day("2025-01-02", _snap(2, 2000.0, 1000.0),
             [_dec("NVDA", rationale="trend ok"), _dec("AAPL", rationale="vol ok")]),
        _day("2025-01-03", _snap(4, 2100.0, 0.0), []),
    ]
    diag = compute_trade_diagnostics(_multiday(days, trade_log))
    assert isinstance(diag, TradeDiagnostics)
    assert diag.real_orders_placed == 0

    by_sym = {t.symbol: t for t in diag.trades}
    nvda = by_sym["NVDA"]
    assert nvda.entry_date == "2025-01-02" and nvda.exit_date == "2025-01-03"
    assert nvda.entry_price == 100.0 and nvda.exit_price == 120.0 and nvda.qty == 10
    assert nvda.pnl == 200.0
    assert nvda.pnl_pct == 0.20
    assert nvda.exit_reason == "trailing_stop_hit"
    assert nvda.entry_evidence is not None and "trend ok" in nvda.entry_evidence

    assert diag.best_trade.symbol == "NVDA"
    assert diag.worst_trade.symbol == "AAPL"
    assert diag.top_symbols_by_pnl[0] == ("NVDA", 200.0)


# --- 미청산 매매: OPEN + 미실현 ---


def test_open_trade_uses_final_prices_for_unrealized():
    trade_log = [_buy("NVDA", 2.5, 100.0)]   # 분수주
    days = [_day("2025-01-02", _snap(1, 1000.0, 250.0), [_dec("NVDA")])]
    diag = compute_trade_diagnostics(_multiday(days, trade_log), final_prices={"NVDA": 140.0})
    leg = diag.trades[0]
    assert leg.exit_reason == "OPEN"
    assert leg.exit_date is None
    assert leg.qty == 2.5
    assert leg.pnl == (140.0 - 100.0) * 2.5      # 미실현 100.0
    assert leg.pnl_pct == 0.40


def test_open_trade_without_final_prices_has_none_pnl():
    trade_log = [_buy("NVDA", 1.0, 100.0)]
    days = [_day("2025-01-02", _snap(1, 1000.0, 100.0), [_dec("NVDA")])]
    diag = compute_trade_diagnostics(_multiday(days, trade_log))
    leg = diag.trades[0]
    assert leg.exit_reason == "OPEN" and leg.pnl is None and leg.pnl_pct is None


# --- drawdown 기간 ---


def test_drawdown_period_peak_trough_recovery():
    # equity: 1000 -> 1200(peak) -> 900(trough) -> 1300(recovery)
    trade_log = []
    days = [
        _day("2025-01-02", _snap(0, 1000.0, 0.0), []),
        _day("2025-01-03", _snap(0, 1200.0, 0.0), []),
        _day("2025-01-06", _snap(0, 900.0, 0.0), []),
        _day("2025-01-07", _snap(0, 1300.0, 0.0), []),
    ]
    diag = compute_trade_diagnostics(_multiday(days, trade_log))
    dd = diag.drawdown
    assert dd is not None
    assert dd.peak_date == "2025-01-03" and dd.peak_equity == 1200.0
    assert dd.trough_date == "2025-01-06" and dd.trough_equity == 900.0
    assert dd.max_drawdown == (1200.0 - 900.0) / 1200.0
    assert dd.recovery_date == "2025-01-07"


def test_drawdown_no_recovery():
    days = [
        _day("2025-01-02", _snap(0, 1000.0, 0.0), []),
        _day("2025-01-03", _snap(0, 800.0, 0.0), []),
    ]
    diag = compute_trade_diagnostics(_multiday(days, []))
    assert diag.drawdown.recovery_date is None


# --- exposure/equity 시퀀스 + veto 집계 ---


def test_exposure_equity_series_and_veto_reasons():
    days = [
        _day("2025-01-02", _snap(0, 1000.0, 300.0),
             [_dec("X", passed=False, reasons=["liquidity 부족"]),
              _dec("Y", passed=False, reasons=["liquidity 부족", "technical 미확인"])]),
        _day("2025-01-03", _snap(0, 1100.0, 250.0),
             [_dec("Z", passed=False, reasons=["liquidity 부족"])]),
    ]
    diag = compute_trade_diagnostics(_multiday(days, []))
    assert diag.equity_over_time == (("2025-01-02", 1000.0), ("2025-01-03", 1100.0))
    assert diag.exposure_over_time == (("2025-01-02", 300.0), ("2025-01-03", 250.0))
    assert diag.top_veto_reasons[0] == ("liquidity 부족", 3)
    txt = format_trade_diagnostics(diag)
    assert "real_orders_placed" in txt and "liquidity 부족" in txt


# --- 실 historical_sim 결과 스모크 ---


def _rows(symbol, close_curve, volume=2_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    vol = np.full(len(close), volume)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": vol,
    })


def test_real_historical_result_smoke():
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, 260)),
        _rows("SPY", np.linspace(300, 400, 260)),
        _rows("BENCH", np.linspace(100, 110, 260)),
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
    diag = compute_trade_diagnostics(res.multiday)
    assert isinstance(diag, TradeDiagnostics)
    assert diag.real_orders_placed == 0
    assert len(diag.equity_over_time) == 3
