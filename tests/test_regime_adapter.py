"""레짐 데이터 어댑터 v1 테스트 — SPY(provider) + VIX(폴백). 주문/네트워크 없음(vix_fetch 주입).

검증: SPY+VIX 정상 레짐 · VIX 없음 SPY-only 폴백 · SPY>200d 위험축소 허용 · SPY<200d 차단 ·
SPY 없음 fail-safe · VIX 폴백 오류 무해 · 라이브 스캔이 VIX 부재에도 전 심볼 skip 아님 ·
승인/주문 미생성 · Robinhood write/Alpaca 거래 미사용.

spec: specs/real_order_v1_checklist.md §18
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.services.live_scan import INSUFFICIENT_DATA, LiveScanLoop
from backend.app.services.market_data import MockMarketDataProvider
import backend.app.services.regime_adapter as ra
from backend.app.services.regime_adapter import RegimeDataAdapter


def _spy_df(up: bool = True, n: int = 260) -> pd.DataFrame:
    i = np.arange(n, dtype=float)
    close = (100.0 + 0.5 * i) if up else (300.0 - 0.5 * i)
    idx = pd.bdate_range(end="2026-06-19", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": np.full(n, 1e6)}, index=idx)


def _adapter(vix):
    return RegimeDataAdapter(object(), vix_fetch=(vix if callable(vix) else (lambda: vix)))


# --- SPY + VIX ---
def test_spy_and_vix_normal_regime():
    r = _adapter(15.0).resolve(spy_bars=_spy_df(up=True))
    assert r.regime == "NORMAL_BULL" and r.regime_source == "spy+vix"
    assert r.effective_regime == "NORMAL_BULL" and r.vix_value == 15.0 and r.risk_reduced is False


def test_spy_and_high_vix_nervous():
    r = _adapter(25.0).resolve(spy_bars=_spy_df(up=True))
    assert r.regime == "NERVOUS_BULL" and r.regime_source == "spy+vix" and r.risk_reduced is True


def test_high_vix_panic():
    r = _adapter(40.0).resolve(spy_bars=_spy_df(up=True))
    assert r.regime == "PANIC" and r.effective_regime == "PANIC"


# --- VIX 없음 → SPY-only ---
def test_vix_missing_spy_bull_fallback():
    r = _adapter(None).resolve(spy_bars=_spy_df(up=True))
    assert r.regime == "spy_bull_vix_unknown" and r.regime_source == "spy_only"
    assert r.effective_regime == "NERVOUS_BULL" and r.risk_reduced is True
    assert r.vix_value is None and any("SPY-only" in w for w in r.warnings)


def test_vix_missing_spy_bear_blocks():
    r = _adapter(None).resolve(spy_bars=_spy_df(up=False))
    assert r.regime == "spy_bear_vix_unknown" and r.regime_source == "spy_only"
    assert r.effective_regime == "BEARISH" and r.risk_reduced is True


# --- fail-safe ---
def test_missing_spy_fails_safe():
    r = _adapter(15.0).resolve(spy_bars=_spy_df(up=True, n=50))  # < 200
    assert r.regime == "insufficient_spy" and r.regime_source == "none" and r.effective_regime is None


def test_vix_fetch_error_does_not_crash():
    def boom():
        raise RuntimeError("vix fetch failed")
    r = RegimeDataAdapter(object(), vix_fetch=boom).resolve(spy_bars=_spy_df(up=True))
    assert r.regime == "spy_bull_vix_unknown" and r.vix_value is None  # 오류 → VIX 없음으로 처리


# --- 라이브 스캔 통합 ---
def test_scan_with_vix_present_yields_candidates(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path, vix_fetch=lambda: 15.0)
    events = loop.scan_cycle(session_id="s", trading_mode="report_only")
    assert events and any(e.buy_candidate for e in events)
    assert all(e.regime_source == "spy+vix" for e in events)


def test_scan_vix_missing_not_all_insufficient(tmp_path):
    # VIX 부재여도 전 심볼 INSUFFICIENT_DATA가 되지 않는다(SPY-only 레짐으로 진행).
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path, vix_fetch=lambda: None)
    events = loop.scan_cycle(session_id="s", trading_mode="report_only")
    assert events and not all(e.scan_status == INSUFFICIENT_DATA for e in events)
    assert all(e.regime_source == "spy_only" for e in events)
    assert any(e.risk_reduced for e in events)
    assert any(e.buy_candidate for e in events)  # SPY-only bull도 진입 허용(보수적)


def test_scan_creates_no_approvals_or_orders(tmp_path):
    LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path, vix_fetch=lambda: None).scan_cycle(
        session_id="s", trading_mode="report_only")
    assert (tmp_path / "live_scan_events.jsonl").exists()
    assert not (tmp_path / "approval_requests.jsonl").exists()
    assert not (tmp_path / "real_execution_receipts.jsonl").exists()
    assert not (tmp_path / "order_router_decisions.jsonl").exists()


# --- 안전 ---
def test_no_robinhood_write_or_alpaca_trading_in_adapter():
    import inspect
    text = inspect.getsource(ra)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text
    # Alpaca 거래 메서드/엔드포인트 미사용(시세 폴백만).
    assert "submit" not in text and "/v2/orders" not in text and "place_order" not in text
