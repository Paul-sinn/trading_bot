"""winner_extension_whatif 테스트 (spec: specs/winner_extension_whatif.md).

수익+건강한 60일 time_stop만 90/120일 연장 what-if(측정 전용). 손실/불건강/미래없음은 연장 안 함.
실 trade_log/포트폴리오 불변. real_orders=0. 네트워크 없음.
"""

import numpy as np
import pandas as pd

from agents.winner_extension_whatif import (
    WinnerExtensionReport,
    compute_selective_winner_extension,
    format_selective_winner_extension,
)
from agents.trade_diagnostics import TradeDiagnostics, TradeLeg

_DATES = pd.date_range("2024-01-01", periods=220, freq="B")


def _df(closes):
    closes = np.asarray(closes, dtype=float)
    idx = _DATES[: len(closes)]
    return pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99,
                         "close": closes}, index=idx)


def _leg(symbol, entry_i, exit_i, entry_px, exit_px, *, reason="time_stop"):
    qty = 1.0
    pnl = (exit_px - entry_px) * qty
    return TradeLeg(symbol=symbol, entry_date=str(_DATES[entry_i].date()),
                    exit_date=str(_DATES[exit_i].date()), entry_price=entry_px,
                    exit_price=exit_px, qty=qty, pnl=pnl, pnl_pct=pnl / entry_px,
                    exit_reason=reason)


def _diag(legs):
    return TradeDiagnostics(trades=tuple(legs), best_trade=None, worst_trade=None, drawdown=None,
                            equity_over_time=(), exposure_over_time=(), top_symbols_by_pnl=(),
                            top_veto_reasons=())


def _cand(report, symbol):
    return next(c for c in report.candidates if c.symbol == symbol)


# 꾸준한 상승 — 건강(가격>20MA>50MA) + 연장 시 이득.
_UPTREND = np.linspace(80, 200, 220)
# 상승 후 하락 — exit 시점 50MA 아래(불건강).
_UP_THEN_DOWN = np.concatenate([np.linspace(100, 190, 110), np.linspace(190, 150, 110)])
_BENCH = pd.Series(np.linspace(100, 130, 220), index=_DATES)


def test_losing_time_stop_never_extended():
    legs = [_leg("LOSE", 60, 120, 150.0, 140.0)]   # pnl<0
    rep = compute_selective_winner_extension(_diag(legs), {"LOSE": _df(_UPTREND)}, benchmark_prices=_BENCH)
    assert isinstance(rep, WinnerExtensionReport)
    assert rep.losing_count == 1
    assert rep.healthy_candidate_count == 0
    assert all(c.symbol != "LOSE" for c in rep.candidates)   # 손실은 후보조차 안 됨(연장 없음)
    assert rep.whatif_pnl_90 is None
    assert rep.real_orders_placed == 0


def test_healthy_profitable_extends_90_120():
    df = _df(_UPTREND)
    entry_px = float(df["close"].iloc[60])
    exit_px = float(df["close"].iloc[120])
    legs = [_leg("WIN", 60, 120, entry_px, exit_px)]
    rep = compute_selective_winner_extension(_diag(legs), {"WIN": df}, benchmark_prices=_BENCH)
    c = _cand(rep, "WIN")
    assert c.healthy is True
    assert c.pnl_90 is not None and c.pnl_120 is not None
    assert c.pnl_90 > c.baseline_pnl                # 상승 지속 → 연장 이득
    assert c.incremental_90 > 0
    assert rep.healthy_candidate_count == 1
    assert rep.whatif_pnl_90 is not None


def test_profitable_but_unhealthy_not_extended():
    df = _df(_UP_THEN_DOWN)
    entry_px = float(df["close"].iloc[60])
    exit_px = float(df["close"].iloc[150])          # 하락 구간 — 여전히 진입가보다 위(수익)지만 50MA 아래
    assert exit_px > entry_px
    legs = [_leg("UNH", 60, 150, entry_px, exit_px)]
    rep = compute_selective_winner_extension(_diag(legs), {"UNH": df}, benchmark_prices=_BENCH)
    c = _cand(rep, "UNH")
    assert c.healthy is False
    assert c.pnl_90 is None and c.pnl_120 is None
    assert len(c.reject_reasons) > 0
    assert rep.profitable_count == 1 and rep.healthy_candidate_count == 0


def test_missing_future_data_fails_safely():
    # exit이 마지막 바 → 미래 데이터 없음 → reject.
    df = _df(_UPTREND[:130])
    entry_px = float(df["close"].iloc[60])
    exit_px = float(df["close"].iloc[129])
    legs = [_leg("NOFUT", 60, 129, entry_px, exit_px)]
    rep = compute_selective_winner_extension(_diag(legs), {"NOFUT": df}, benchmark_prices=_BENCH)
    c = _cand(rep, "NOFUT")
    assert c.healthy is False
    assert "no_future_data" in c.reject_reasons
    assert c.pnl_90 is None


def test_non_time_stop_exits_ignored():
    legs = [_leg("X", 60, 120, 100.0, 150.0, reason="trailing_stop_hit")]
    rep = compute_selective_winner_extension(_diag(legs), {"X": _df(_UPTREND)}, benchmark_prices=_BENCH)
    assert rep.num_time_stop_exits == 0
    assert rep.candidates == ()


def test_missing_price_data_safe():
    legs = [_leg("GHOST", 60, 120, 100.0, 150.0)]
    rep = compute_selective_winner_extension(_diag(legs), {}, benchmark_prices=_BENCH)
    c = _cand(rep, "GHOST")
    assert c.healthy is False
    assert "no_price_data" in c.reject_reasons
    assert rep.real_orders_placed == 0


def test_inputs_not_mutated():
    df = _df(_UPTREND)
    legs = [_leg("WIN", 60, 120, float(df["close"].iloc[60]), float(df["close"].iloc[120]))]
    diag = _diag(legs)
    before = diag.trades
    df_before = df.copy()
    pd_data = {"WIN": df}
    compute_selective_winner_extension(diag, pd_data, benchmark_prices=_BENCH)
    assert diag.trades == before
    pd.testing.assert_frame_equal(pd_data["WIN"], df_before)


def test_format_contains_sections():
    df = _df(_UPTREND)
    legs = [_leg("WIN", 60, 120, float(df["close"].iloc[60]), float(df["close"].iloc[120]))]
    rep = compute_selective_winner_extension(_diag(legs), {"WIN": df}, benchmark_prices=_BENCH)
    text = format_selective_winner_extension(rep)
    assert "Winner Extension" in text
    assert "90" in text and "120" in text
    assert "real_orders_placed : 0" in text
