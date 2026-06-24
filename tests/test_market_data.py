"""MarketDataProvider 추상화 테스트 (spec: specs/live_scan.md).

기본 provider=mock, 알 수 없는 provider fail-closed, mock 결정론, free provider graceful 실패.
브로커·Robinhood·유료 API·LLM 없음.
"""

from __future__ import annotations

import pytest

from agents.data_adapter import FreeDailyProvider
from backend.app.core.config import Settings
from backend.app.services.market_data import (
    FreeMarketDataProvider,
    MarketDataProviderNotConfigured,
    MockMarketDataProvider,
    get_market_data_provider,
)


def test_default_provider_is_mock():
    # 코드 기본값이 mock인지 검증(.env의 MARKET_DATA_PROVIDER 영향 배제 — hermetic).
    assert Settings.model_fields["market_data_provider"].default == "mock"
    provider = get_market_data_provider(Settings(market_data_provider="mock"))
    assert provider.name == "mock"
    assert provider.provider_status().available is True


def test_unknown_provider_fails_closed():
    with pytest.raises(MarketDataProviderNotConfigured):
        get_market_data_provider(Settings(market_data_provider="bloomberg"))


def test_free_provider_selected():
    provider = get_market_data_provider(Settings(market_data_provider="free"))
    assert provider.name == "free"


def test_mock_is_deterministic():
    a = MockMarketDataProvider().get_recent_bars("AAPL", lookback_days=250)
    b = MockMarketDataProvider().get_recent_bars("AAPL", lookback_days=250)
    assert list(a["close"]) == list(b["close"])
    assert len(a) == 250


def test_mock_quote_matches_last_bar():
    provider = MockMarketDataProvider()
    q = provider.get_quote("NVDA")
    bars = provider.get_recent_bars("NVDA", lookback_days=1)
    assert q.price == pytest.approx(float(bars["close"].iloc[-1]))


def test_mock_provides_spy_and_vix():
    provider = MockMarketDataProvider()
    spy = provider.get_recent_bars("SPY", lookback_days=260)
    vix = provider.get_recent_bars("^VIX", lookback_days=10)
    assert len(spy) >= 200 and "close" in spy.columns
    assert len(vix) > 0 and float(vix["close"].iloc[-1]) == 15.0


def test_free_provider_fails_gracefully_on_fetch_error():
    # 네트워크/데이터 실패를 주입(fetch_fn이 예외) → provider가 크래시하지 않고 graceful.
    def _boom(symbol, start, end):
        raise ConnectionError("no network")

    free = FreeMarketDataProvider(daily=FreeDailyProvider(fetch_fn=_boom))
    # get_quotes는 심볼 실패를 건너뛴다(빈 dict, 크래시 없음).
    assert free.get_quotes(["AAPL", "MSFT"]) == {}
    # provider_status는 fetch_fn 주입 시 available True(네트워크 호출 없이 판정).
    assert free.provider_status().available is True
    # 단일 get_recent_bars는 예외를 전파하지만(호출부 LiveScanLoop가 ERROR로 흡수) — 여기서는 확인만.
    with pytest.raises(Exception):
        free.get_recent_bars("AAPL")


def test_free_provider_status_unavailable_when_no_yfinance(monkeypatch):
    # yfinance 미설치 시 provider_status.available=False(graceful), 예외 없음.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("no yfinance")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    free = FreeMarketDataProvider()  # fetch_fn 미주입 → import 경로
    status = free.provider_status()
    assert status.available is False
    assert "yfinance" in status.detail
