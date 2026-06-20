"""event_impact 테스트 (spec: specs/event_impact.md).

이벤트로 차단된 후보 집계 + bypass/events 비교. 측정 전용 — 매매 불변. real_orders=0. 네트워크 없음.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agents.event_calendar import EventCalendarProvider
from agents.event_impact import (
    EVENT_VETO_SUBSTR,
    EventImpactReport,
    RunComparison,
    compare_runs,
    compute_event_impact,
    format_comparison,
    format_event_impact,
)
from agents.evidence import EvidenceParams
from agents.historical_sim import run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_EVENT_REASON = "earnings/FOMC/CPI 등 " + EVENT_VETO_SUBSTR  # policy 실제 사유 형태


# --- fixture stand-in ---


def _dec(symbol, *, raw="BUY", reasons=()):
    veto = SimpleNamespace(passed=(len(reasons) == 0), reasons=tuple(reasons))
    return SimpleNamespace(symbol=symbol, raw_decision=SimpleNamespace(name=raw), veto=veto)


def _day(date, decisions):
    return SimpleNamespace(report=SimpleNamespace(report_date=date, decisions=tuple(decisions)))


def _multiday(day_results, trade_log=()):
    return SimpleNamespace(
        day_results=tuple(day_results),
        portfolio=SimpleNamespace(trade_log=tuple(trade_log)),
    )


# --- 차단 집계 ---


def test_blocked_candidates_aggregated():
    days = [
        _day("2026-03-20", [_dec("NVDA", reasons=[_EVENT_REASON]),
                            _dec("MSFT", reasons=[_EVENT_REASON])]),
        _day("2026-03-21", [_dec("NVDA", reasons=[_EVENT_REASON])]),
    ]
    rep = compute_event_impact(_multiday(days))
    assert isinstance(rep, EventImpactReport)
    assert rep.num_blocked == 3
    assert dict(rep.by_symbol) == {"NVDA": 2, "MSFT": 1}
    assert dict(rep.by_date) == {"2026-03-20": 2, "2026-03-21": 1}
    assert rep.symbols_affected == ("MSFT", "NVDA")
    assert rep.real_orders_placed == 0


def test_would_have_been_buy_only_when_event_is_sole_reason():
    days = [_day("2026-03-20", [
        _dec("A", raw="BUY", reasons=[_EVENT_REASON]),                       # 이벤트 유일 → True
        _dec("B", raw="BUY", reasons=[_EVENT_REASON, "liquidity 부족"]),     # 다른 사유 → False
        _dec("C", raw="HOLD", reasons=[_EVENT_REASON]),                      # raw HOLD → False
    ])]
    rep = compute_event_impact(_multiday(days))
    assert rep.num_blocked == 3
    assert rep.would_be_buy_count == 1
    by = {b.symbol: b.would_have_been_buy for b in rep.blocked}
    assert by == {"A": True, "B": False, "C": False}


def test_medium_low_or_passing_not_counted_as_blocked():
    days = [_day("2026-04-10", [
        _dec("AAPL", reasons=[]),                       # 통과(medium/low는 차단 안 됨)
        _dec("MSFT", reasons=["liquidity 부족"]),       # 비이벤트 veto
    ])]
    rep = compute_event_impact(_multiday(days))
    assert rep.num_blocked == 0
    assert rep.symbols_affected == ()


def test_enrichment_via_provider():
    ev = pd.DataFrame(
        [["2026-03-20", "FOMC", "MARKET", "high", "Fed"]],
        columns=["date", "event_type", "ticker", "severity", "notes"],
    )
    prov = EventCalendarProvider.from_frame(ev)
    days = [_day("2026-03-20", [_dec("AAPL", reasons=[_EVENT_REASON])])]
    rep = compute_event_impact(_multiday(days), event_provider=prov)
    b = rep.blocked[0]
    assert b.event_type == "FOMC" and b.severity == "high"
    assert dict(rep.by_event_type) == {"FOMC": 1}


def test_diagnostics_do_not_change_trades():
    trade_log = (SimpleNamespace(symbol="NVDA", side="buy"),)
    md = _multiday([_day("2026-03-20", [_dec("NVDA", reasons=[_EVENT_REASON])])], trade_log)
    rep = compute_event_impact(md)
    assert md.portfolio.trade_log == trade_log     # 불변
    assert rep.real_orders_placed == 0
    assert "Event Impact" in format_event_impact(rep)


# --- 비교 ---


def _result(num_trades, cum, mdd, buys):
    perf = SimpleNamespace(num_trades=num_trades, cumulative_return=cum, max_drawdown=mdd)
    trade_log = tuple(SimpleNamespace(symbol=s, side="buy") for s in buys)
    return SimpleNamespace(performance=perf, portfolio=SimpleNamespace(trade_log=trade_log))


def test_compare_runs_diff_and_symbols_affected():
    bypass = _result(5, 0.30, 0.26, ["NVDA", "AAPL", "MSFT"])
    events = _result(3, 0.22, 0.10, ["NVDA", "MSFT"])      # AAPL 진입이 이벤트로 빠짐
    cmp = compare_runs(bypass, events)
    assert isinstance(cmp, RunComparison)
    assert cmp.trade_count_diff == -2
    assert cmp.cumulative_return_diff == pytest.approx(-0.08)
    assert cmp.max_drawdown_diff == pytest.approx(-0.16)
    assert cmp.symbols_affected == ("AAPL",)
    assert cmp.real_orders_placed == 0
    assert "Run Comparison" in format_comparison(cmp)


# --- 실 historical_sim 통합 ---


def _spiked_volume(n, *, base=1_000_000.0, spike=6_000_000.0, last=3):
    """마지막 last개 거래일에 거래량 급등 — volume_spike 필터(>1.5×20d) 통과용."""
    v = np.full(n, base)
    v[-last:] = spike
    return v


def _rows(symbol, close_curve, volume=2_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    vol = np.asarray(volume, dtype=float)
    if vol.ndim == 0:
        vol = np.full(len(close), float(volume))
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": vol,
    })


def _setup():
    # NVDA/MSFT는 스캔 대상 — 상승추세(UP) + 마지막 3일 거래량 급등으로 실제 후보가 생성되게 한다.
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, 260), volume=_spiked_volume(260)),
        _rows("MSFT", np.linspace(90, 240, 260), volume=_spiked_volume(260)),
        _rows("SPY", np.linspace(300, 400, 260)),
        _rows("BENCH", np.linspace(100, 110, 260)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    return data, spy, vix, list(spy.index[-3:])


def _run(data, spy, vix, days, provider):
    return asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"], "MSFT": data["MSFT"]},
        spy_prices=spy, vix=vix, policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0), event_provider=provider,
    ))


def test_market_high_event_in_blocked_diagnostics():
    data, spy, vix, days = _setup()
    ev = pd.DataFrame(
        [[str(d.date()), "FOMC", "MARKET", "high", "blocked"] for d in days],
        columns=["date", "event_type", "ticker", "severity", "notes"],
    )
    prov = EventCalendarProvider.from_frame(ev)
    res = _run(data, spy, vix, days, prov)
    rep = compute_event_impact(res.multiday, event_provider=prov)
    assert rep.num_blocked > 0
    assert dict(rep.by_event_type).get("FOMC", 0) == rep.num_blocked   # 전부 FOMC
    assert set(rep.symbols_affected) <= {"NVDA", "MSFT"}
    assert "NVDA" in rep.symbols_affected
    assert res.real_orders_placed == 0


def test_ticker_event_blocks_only_matching_symbol():
    data, spy, vix, days = _setup()
    ev = pd.DataFrame(
        [[str(d.date()), "earnings", "MSFT", "high", "msft only"] for d in days],
        columns=["date", "event_type", "ticker", "severity", "notes"],
    )
    prov = EventCalendarProvider.from_frame(ev)
    res = _run(data, spy, vix, days, prov)
    rep = compute_event_impact(res.multiday, event_provider=prov)
    assert "MSFT" in rep.symbols_affected      # ticker 이벤트 대상만
    assert "NVDA" not in rep.symbols_affected  # 다른 심볼은 영향 없음
    assert res.real_orders_placed == 0
