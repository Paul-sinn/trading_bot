"""run_sim 청산(exit) 배선 테스트 (spec: specs/exit_wiring.md).

기존 sim_exit 재사용. 플래그 없으면 OPEN 유지(기본 불변), 있으면 stop/trailing/time 청산. vetoed 후보는
매매 0. real_orders_placed=0. 전략 미변경. 네트워크/브로커 없음.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import run_historical_simulation
from agents.multiday import DayInput, run_phase1_multiday
from agents.policy_loader import load_policy
from agents.price_csv import close_series, load_price_data_from_frame
from agents.sim_exit import ExitPolicy, exit_params_for_position
from agents.sim_portfolio import SimulatedPortfolio

REAL_CONFIG = Path(__file__).resolve().parents[1] / "config"


class _NoScanner:
    async def scan(self):
        return []


def _seed(pf: SimulatedPortfolio, symbol: str, shares: float, price: float):
    """포지션을 직접 시드한다(체결 객체는 apply_buy_fill가 읽는 필드만)."""
    fill = SimpleNamespace(
        symbol=symbol, estimated_shares=shares, fill_price=price, filled_notional=shares * price
    )
    pf.apply_buy_fill(fill)


def _day(date, prices):
    return DayInput(date=date, scanner=_NoScanner(), contexts={}, mark_prices=prices)


def _run(pf, days, exit_policy):
    return asyncio.run(run_phase1_multiday(
        days=days, policy=load_policy(REAL_CONFIG), portfolio=pf, exit_policy=exit_policy
    ))


def _sells(pf):
    return [t for t in pf.trade_log if t.side == "sell"]


# --- ExitPolicy / 변환 단위 ---


def test_exit_policy_validation_fail_closed():
    with pytest.raises(ValueError):
        ExitPolicy(stop_loss_pct=0.0)
    with pytest.raises(ValueError):
        ExitPolicy(stop_loss_pct=1.0)
    with pytest.raises(ValueError):
        ExitPolicy(trail_pct=-0.1)
    with pytest.raises(ValueError):
        ExitPolicy(max_hold_days=0)
    assert not ExitPolicy().is_active
    assert ExitPolicy(stop_loss_pct=0.1).is_active


def test_exit_params_for_position_maps_stop_and_time():
    ep = ExitPolicy(stop_loss_pct=0.1, max_hold_days=3)
    params = exit_params_for_position(ep, avg_entry_price=100.0, hold_days=2)
    assert params.stop_price == 90.0
    assert params.max_hold_days == 3 and params.hold_days == 2


# --- 기본(플래그 없음) 동작 불변 ---


def test_no_exit_flags_positions_stay_open():
    pf = SimulatedPortfolio(10_000.0)
    _seed(pf, "NVDA", 1, 100.0)
    res = _run(pf, [_day("2025-01-02", {"NVDA": 50.0})], exit_policy=None)  # 큰 하락에도
    assert "NVDA" in pf.positions          # 청산 안 함 — OPEN 유지
    assert _sells(pf) == []
    assert res.real_orders_placed == 0


# --- stop-loss ---


def test_stop_loss_exits_losing_trade():
    pf = SimulatedPortfolio(10_000.0)
    _seed(pf, "NVDA", 1, 100.0)
    res = _run(pf, [_day("2025-01-02", {"NVDA": 85.0})], ExitPolicy(stop_loss_pct=0.10))
    sells = _sells(pf)
    assert len(sells) == 1
    assert sells[0].exit_reason == "stop_loss_hit"
    assert sells[0].realized_pnl == (85.0 - 100.0) * 1
    assert "NVDA" not in pf.positions
    assert res.real_orders_placed == 0


# --- trailing-stop ---


def test_trailing_stop_exits_after_drawdown_from_high():
    pf = SimulatedPortfolio(10_000.0)
    _seed(pf, "NVDA", 1, 100.0)
    days = [
        _day("2025-01-02", {"NVDA": 130.0}),   # 고점 130
        _day("2025-01-03", {"NVDA": 116.0}),   # 130*0.9=117 아래 → 청산
    ]
    res = _run(pf, days, ExitPolicy(trail_pct=0.10))
    sells = _sells(pf)
    assert len(sells) == 1
    assert sells[0].exit_reason == "trailing_stop_hit"
    assert "NVDA" not in pf.positions
    assert res.real_orders_placed == 0


# --- max-holding-days ---


def test_max_holding_days_exits_old_position():
    pf = SimulatedPortfolio(10_000.0)
    _seed(pf, "NVDA", 1, 100.0)
    days = [
        _day("2025-01-02", {"NVDA": 100.0}),   # hold=1 < 2 → 미청산
        _day("2025-01-03", {"NVDA": 105.0}),   # hold=2 >= 2 → 시간청산
    ]
    res = _run(pf, days, ExitPolicy(max_hold_days=2))
    sells = _sells(pf)
    assert len(sells) == 1
    assert sells[0].exit_reason == "time_stop"
    assert "NVDA" not in pf.positions
    assert res.real_orders_placed == 0


# --- vetoed 후보는 청산 켜져도 매매 0 ---


def _rows(symbol, close_curve, volume=2_000_000.0):
    close = np.asarray(close_curve, dtype=float)
    return pd.DataFrame({
        "symbol": symbol, "date": pd.date_range("2024-01-01", periods=len(close), freq="B"),
        "open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
        "volume": np.full(len(close), volume),
    })


def test_vetoed_candidates_create_no_trades_even_with_exit_policy():
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
        event_provider=None,                          # 이벤트 결측 → fail-closed veto
        exit_policy=ExitPolicy(stop_loss_pct=0.10),   # 청산 켜져 있어도
    ))
    assert res.portfolio.positions == {}              # 진입 0
    assert res.portfolio.trade_log == ()              # 매매 0
    assert res.real_orders_placed == 0


# --- CLI 플래그 배선 ---


def test_run_sim_exit_flags_parse_and_build():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_sim  # noqa: E402

    parser = run_sim.build_arg_parser()
    args = parser.parse_args([
        "--data-root", "data/x", "--stop-loss-pct", "0.1",
        "--trailing-stop-pct", "0.15", "--max-holding-days", "5",
        "--manual-exit-date", "2025-06-20",
    ])
    assert args.stop_loss_pct == 0.1 and args.trailing_stop_pct == 0.15
    assert args.max_holding_days == 5 and args.manual_exit_date == "2025-06-20"
    policy = run_sim._build_exit_policy(args)
    assert policy is not None and policy.is_active

    # 기본은 None(기존 동작 불변).
    assert run_sim._build_exit_policy(parser.parse_args(["--data-root", "data/x"])) is None

    # 잘못된 값 → fail-closed.
    with pytest.raises(run_sim.DataAdapterError):
        run_sim._build_exit_policy(parser.parse_args(["--data-root", "data/x", "--stop-loss-pct", "1.5"]))
