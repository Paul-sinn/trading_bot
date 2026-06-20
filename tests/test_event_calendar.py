"""이벤트 캘린더 CSV provider 테스트 (spec: specs/event_calendar.md).

로컬 events.csv → is_clear. MARKET=전 심볼, ticker=해당 심볼, high만 차단. 결측/무효 fail-closed.
run_sim 배선(둘 다 없으면 fail-closed, --assume-no-events 바이패스 유지). real_orders=0. 네트워크 없음.
"""

import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents.event_calendar import (
    EventCalendarError,
    EventCalendarProvider,
)
from agents.evidence import EvidenceParams
from agents.historical_sim import run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


def _frame(rows):
    return pd.DataFrame(rows, columns=["date", "event_type", "ticker", "severity", "notes"])


_EVENTS = _frame([
    ["2026-01-28", "earnings", "MSFT", "high", "quarterly earnings"],
    ["2026-03-20", "FOMC", "MARKET", "high", "Fed decision"],
    ["2026-04-10", "CPI", "MARKET", "medium", "inflation report"],
])


# --- 로드 + 조회 ---


def test_valid_events_load():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    assert isinstance(prov, EventCalendarProvider)


def test_market_event_affects_all_symbols():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    d = pd.Timestamp("2026-03-20")          # FOMC MARKET high
    assert prov.is_clear("AAPL", d) is False
    assert prov.is_clear("MSFT", d) is False
    assert prov.is_clear("NVDA", d) is False


def test_ticker_event_affects_only_matching_symbol():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    d = pd.Timestamp("2026-01-28")          # MSFT earnings high
    assert prov.is_clear("MSFT", d) is False
    assert prov.is_clear("AAPL", d) is True  # 다른 심볼은 clear


def test_only_high_severity_blocks():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    d = pd.Timestamp("2026-04-10")          # CPI MARKET medium → 차단 안 함
    assert prov.is_clear("AAPL", d) is True
    assert prov.is_clear("MSFT", d) is True


def test_clear_on_non_event_date():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    assert prov.is_clear("MSFT", pd.Timestamp("2026-02-15")) is True


def test_as_of_none_fails_closed():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    assert prov.is_clear("MSFT", None) is False   # 날짜 불명 → 확인 불가 → 미확인


def test_events_on_returns_evidence():
    prov = EventCalendarProvider.from_frame(_EVENTS)
    hits = prov.events_on("MSFT", pd.Timestamp("2026-01-28"))
    assert len(hits) == 1 and hits[0].event_type == "earnings"


def test_csv_roundtrip(tmp_path):
    p = tmp_path / "events.csv"
    _EVENTS.to_csv(p, index=False)
    prov = EventCalendarProvider.from_csv(p)
    assert prov.is_clear("MSFT", pd.Timestamp("2026-01-28")) is False


# --- fail-closed (malformed) ---


def test_missing_column_fails_closed():
    bad = _EVENTS.drop(columns=["severity"])
    with pytest.raises(EventCalendarError):
        EventCalendarProvider.from_frame(bad)


def test_invalid_date_fails_closed():
    bad = _EVENTS.copy()
    bad.loc[0, "date"] = "not-a-date"
    with pytest.raises(EventCalendarError):
        EventCalendarProvider.from_frame(bad)


def test_missing_file_fails_closed():
    with pytest.raises(EventCalendarError):
        EventCalendarProvider.from_csv("does_not_exist_99.csv")


# --- run_sim 배선 ---


def _run_sim():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_sim  # noqa: E402
    return run_sim


def test_run_sim_fails_closed_without_events_or_bypass():
    run_sim = _run_sim()
    args = run_sim.build_arg_parser().parse_args(["--data-root", "data/x"])
    with pytest.raises(run_sim.DataAdapterError):
        run_sim._resolve_event_provider(args)


def test_run_sim_assume_no_events_bypass_still_works():
    run_sim = _run_sim()
    args = run_sim.build_arg_parser().parse_args(["--data-root", "data/x", "--assume-no-events"])
    prov = run_sim._resolve_event_provider(args)
    assert prov.is_clear("ANY", None) is True       # 바이패스 = 항상 clear


def test_run_sim_events_csv_resolves_provider(tmp_path):
    run_sim = _run_sim()
    p = tmp_path / "events.csv"
    _EVENTS.to_csv(p, index=False)
    args = run_sim.build_arg_parser().parse_args(["--data-root", "data/x", "--events-csv", str(p)])
    prov = run_sim._resolve_event_provider(args)
    assert isinstance(prov, EventCalendarProvider)


def test_run_sim_malformed_events_csv_fails_closed(tmp_path):
    run_sim = _run_sim()
    p = tmp_path / "bad.csv"
    _EVENTS.drop(columns=["ticker"]).to_csv(p, index=False)
    args = run_sim.build_arg_parser().parse_args(["--data-root", "data/x", "--events-csv", str(p)])
    with pytest.raises(run_sim.DataAdapterError):
        run_sim._resolve_event_provider(args)


# --- historical_sim 통합: MARKET high가 전 심볼 차단 + real_orders 0 ---


def _rows(symbol, close_curve, volume=2_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.full(len(close), volume),
    })


def test_market_high_event_blocks_all_entries_in_historical_sim():
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, 260)),
        _rows("SPY", np.linspace(300, 400, 260)),
        _rows("BENCH", np.linspace(100, 110, 260)),
    ], ignore_index=True)
    data = load_price_data_from_frame(frame)
    spy = close_series(data, "SPY")
    vix = pd.Series(np.full(len(spy), 15.0), index=spy.index)
    days = list(spy.index[-3:])

    # 거래일 전부에 MARKET high 이벤트 → 전 심볼 차단.
    ev = _frame([[str(d.date()), "FOMC", "MARKET", "high", "blocked"] for d in days])
    provider = EventCalendarProvider.from_frame(ev)

    res = asyncio.run(run_historical_simulation(
        price_data={"NVDA": data["NVDA"]}, spy_prices=spy, vix=vix,
        policy=load_policy(REAL_CONFIG), account_cash=1_000_000.0,
        benchmark_prices=close_series(data, "BENCH"), trading_days=days,
        params=EvidenceParams(account_equity=1_000_000.0),
        event_provider=provider,
    ))
    assert res.portfolio.positions == {}     # MARKET high → 전부 veto → 진입 0
    assert res.real_orders_placed == 0
