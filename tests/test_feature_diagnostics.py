"""feature_diagnostics 테스트 (spec: specs/feature_diagnostics.md).

트레이드 진입 시점의 FeatureSnapshot을 진단에 노출(측정 전용). point-in-time 슬라이스(미래참조 없음).
매매/veto 불변. 데이터 부족/없음 fail-safe. real_orders=0. 네트워크 없음.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.feature_diagnostics import (
    FeatureDiagnostics,
    FeatureRow,
    compute_feature_diagnostics,
    format_feature_diagnostics,
)
from agents.historical_sim import run_historical_simulation
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from pathlib import Path

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"
_DATES = pd.date_range("2024-01-01", periods=260, freq="B")


def _ohlcv(close, *, volume=1_000_000.0):
    close = np.asarray(close, dtype=float)
    vol = np.asarray(volume, dtype=float)
    if vol.ndim == 0:
        vol = np.full(len(close), float(volume))
    return pd.DataFrame(
        {
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": vol,
        },
        index=_DATES[: len(close)],
    )


def _trade(symbol, entry_date):
    return SimpleNamespace(symbol=symbol, entry_date=entry_date)


# --- 직접 주입(source_trades) 단위 테스트 ---


def test_features_computed_for_trades():
    price_data = {"NVDA": _ohlcv(np.linspace(80, 200, 260))}
    bench = pd.Series(np.linspace(100, 110, 260), index=_DATES)
    entry = str(_DATES[-1].date())
    diag = compute_feature_diagnostics(
        None, price_data, benchmark_prices=bench,
        source_trades=[_trade("NVDA", entry)],
    )
    assert isinstance(diag, FeatureDiagnostics)
    assert len(diag.rows) == 1
    row = diag.rows[0]
    assert isinstance(row, FeatureRow)
    assert row.snapshot is not None
    assert row.snapshot.momentum_score is not None
    assert row.snapshot.return_1m is not None
    assert row.snapshot.relative_strength is not None      # 벤치마크 제공
    assert row.snapshot.price_above_20ma is True
    assert row.snapshot.missing_fields == ()
    assert diag.real_orders_placed == 0


def test_point_in_time_slice_no_lookahead():
    price_data = {"NVDA": _ohlcv(np.linspace(80, 200, 260))}
    mid = str(_DATES[150].date())
    diag = compute_feature_diagnostics(
        None, price_data, source_trades=[_trade("NVDA", mid)]
    )
    # 슬라이스가 entry_date까지만 → as_of가 그 날짜(마지막 바 아님).
    assert diag.rows[0].snapshot.as_of == mid


def test_insufficient_history_reports_missing_fields():
    price_data = {"NVDA": _ohlcv(np.linspace(100, 110, 30))}  # 6m(126) 불가
    entry = str(_DATES[29].date())
    diag = compute_feature_diagnostics(
        None, price_data, source_trades=[_trade("NVDA", entry)]
    )
    snap = diag.rows[0].snapshot
    assert snap is not None
    assert "return_6m" in snap.missing_fields
    assert "momentum_score" in snap.missing_fields
    assert snap.return_1m is not None


def test_symbol_without_price_data_is_safe():
    diag = compute_feature_diagnostics(
        None, {"NVDA": _ohlcv(np.linspace(80, 200, 260))},
        source_trades=[_trade("MSFT", str(_DATES[-1].date()))],
    )
    row = diag.rows[0]
    assert row.snapshot is None
    assert row.note is not None
    assert diag.real_orders_placed == 0


def test_duplicate_symbol_date_deduped():
    price_data = {"NVDA": _ohlcv(np.linspace(80, 200, 260))}
    entry = str(_DATES[-1].date())
    diag = compute_feature_diagnostics(
        None, price_data,
        source_trades=[_trade("NVDA", entry), _trade("NVDA", entry)],
    )
    assert len(diag.rows) == 1


def test_format_contains_features():
    price_data = {"NVDA": _ohlcv(np.linspace(80, 200, 260))}
    diag = compute_feature_diagnostics(
        None, price_data, source_trades=[_trade("NVDA", str(_DATES[-1].date()))]
    )
    text = format_feature_diagnostics(diag)
    assert "Feature" in text
    assert "NVDA" in text
    assert "mom" in text.lower()        # 모멘텀 컬럼 헤더


# --- 실 historical_sim 통합: 진단이 매매/veto를 바꾸지 않음 ---


def _rows(symbol, close_curve, volume):
    close = np.asarray(close_curve, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": _DATES[: len(close)],
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.asarray(volume, dtype=float),
    })


def _spiked(n, base=1_000_000.0, spike=6_000_000.0, last=3):
    v = np.full(n, base)
    v[-last:] = spike
    return v


def _run_sim_with_trades():
    frame = pd.concat([
        _rows("NVDA", np.linspace(80, 200, 260), _spiked(260)),
        _rows("SPY", np.linspace(300, 400, 260), np.full(260, 1_000_000.0)),
        _rows("BENCH", np.linspace(100, 110, 260), np.full(260, 1_000_000.0)),
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
    return res, {"NVDA": data["NVDA"]}, close_series(data, "BENCH")


def test_diagnostics_do_not_change_trades_or_vetoes():
    res, price_data, bench = _run_sim_with_trades()
    trade_log_before = tuple(res.multiday.portfolio.trade_log)
    vetoes_before = tuple(
        (d.symbol, d.veto.passed, tuple(d.veto.reasons))
        for dr in res.multiday.day_results for d in dr.report.decisions
    )

    diag = compute_feature_diagnostics(res.multiday, price_data, benchmark_prices=bench)

    assert tuple(res.multiday.portfolio.trade_log) == trade_log_before   # 매매 불변
    vetoes_after = tuple(
        (d.symbol, d.veto.passed, tuple(d.veto.reasons))
        for dr in res.multiday.day_results for d in dr.report.decisions
    )
    assert vetoes_after == vetoes_before                                 # veto 불변
    assert diag.real_orders_placed == 0
    assert res.real_orders_placed == 0


def test_integration_rows_from_multiday_trades():
    res, price_data, bench = _run_sim_with_trades()
    diag = compute_feature_diagnostics(res.multiday, price_data, benchmark_prices=bench)
    # 트레이드가 있으면 그 심볼의 피처 행이 생긴다(없으면 빈 진단 — fail-safe).
    for row in diag.rows:
        assert row.symbol == "NVDA"
        assert row.snapshot is None or row.snapshot.symbol == "NVDA"
