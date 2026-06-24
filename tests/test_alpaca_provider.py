"""Alpaca 시장데이터 provider v1 테스트 — 시세 전용, 주문 없음. 네트워크 없이 http_get 주입.

검증: env 로드 · 키 미설정 fail-safe · bars/quote 정규화 · MARKET_DATA_PROVIDER=alpaca 선택 ·
라이브 스캔이 Alpaca provider 사용 · Alpaca 오류가 후보를 안전 차단 · 라우터가 Alpaca 호가 사용 ·
실 MCP write 도구명 미포함 · Alpaca 거래 비활성.

spec: specs/real_order_v1_checklist.md §16
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.core.config import Settings
from backend.app.services.live_scan import LiveScanLoop
import backend.app.services.market_data as md
from backend.app.services.market_data import (
    AlpacaMarketDataProvider,
    AlpacaNotConfigured,
    MarketQuote,
    get_market_data_provider,
)

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)


def _settings(**kw) -> Settings:
    base = dict(market_data_provider="alpaca", alpaca_api_key_id="KEY", alpaca_api_secret_key="SECRET",
                alpaca_data_feed="iex", alpaca_bar_timeframe="1Day", alpaca_lookback_days=300)
    base.update(kw)
    return Settings(**base)


def _bars_payload(n: int, *, start: float = 100.0, step: float = 0.5) -> dict:
    rows = []
    for i in range(n):
        c = start + step * i
        rows.append({"t": f"2024-01-{(i % 27) + 1:02d}T05:00:00Z", "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1_000_000})
    return {"bars": rows}


def _fake_http(*, bars_n: int = 250, raise_on=None):
    def http_get(url: str, headers: dict, params: dict) -> dict:
        assert headers.get("APCA-API-KEY-ID") and headers.get("APCA-API-SECRET-KEY")  # 키 전달 확인
        if raise_on and raise_on in url:
            raise RuntimeError("alpaca error")
        if "/bars" in url:
            return _bars_payload(bars_n)
        if "/trades/latest" in url:
            return {"trade": {"p": 14.05, "t": "2026-06-23T14:59:59Z"}}
        if "/quotes/latest" in url and params.get("symbols"):
            syms = params["symbols"].split(",")
            return {"quotes": {s: {"bp": 14.0, "ap": 14.02, "t": "2026-06-23T14:59:59Z"} for s in syms}}
        if "/quotes/latest" in url:
            return {"quote": {"bp": 14.0, "ap": 14.02, "t": "2026-06-23T14:59:59Z"}}
        return {}
    return http_get


# --- env / 가용성 ---
def test_provider_loads_env():
    p = AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http())
    st = p.provider_status()
    assert st.name == "alpaca" and st.available is True and "iex" in st.detail


def test_missing_env_fails_safely():
    # .env에 실제 키가 있을 수 있으므로 명시적으로 빈 값으로 덮어 미설정 상태를 강제한다.
    p = AlpacaMarketDataProvider(settings=Settings(market_data_provider="alpaca",
                                                   alpaca_api_key_id="", alpaca_api_secret_key=""))
    assert p.provider_status().available is False
    with pytest.raises(AlpacaNotConfigured):
        p.get_recent_bars("AAPL", lookback_days=300)


# --- 정규화 ---
def test_bars_normalize():
    p = AlpacaMarketDataProvider(settings=_settings(),
                                 http_get=lambda u, h, pr: _bars_payload(3, start=10.0, step=1.0))
    df = p.get_recent_bars("F", lookback_days=300)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3 and df["close"].tolist() == [10.0, 11.0, 12.0]
    assert df.attrs["source"] == "alpaca" and df.attrs["feed"] == "iex"


def test_empty_bars_returns_empty_frame():
    p = AlpacaMarketDataProvider(settings=_settings(), http_get=lambda u, h, pr: {"bars": []})
    df = p.get_recent_bars("F")
    assert df.empty and "close" in df.columns


def test_quote_normalize():
    p = AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http())
    q = p.get_latest_quote("F")
    assert isinstance(q, MarketQuote)
    assert q.symbol == "F" and q.bid == 14.0 and q.ask == 14.02 and q.last == 14.05
    assert q.source == "alpaca" and q.feed == "iex" and q.quote_timestamp


def test_batch_quote_normalize():
    p = AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http())
    out = p.get_batch_latest_quotes(["F", "AAPL"])
    assert set(out) == {"F", "AAPL"} and out["F"].ask == 14.02 and out["F"].source == "alpaca"


# --- 선택 / 스캔 통합 ---
def test_provider_selection_alpaca():
    prov = get_market_data_provider(_settings())
    assert prov.name == "alpaca" and isinstance(prov, AlpacaMarketDataProvider)


def test_live_scan_uses_alpaca(tmp_path):
    prov = AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http(bars_n=250))
    loop = LiveScanLoop(prov, reports_dir=tmp_path, max_symbols=3)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    assert events and all(e.provider == "alpaca" for e in events)


def test_alpaca_error_blocks_candidate(tmp_path):
    prov = AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http(raise_on="/bars"))
    loop = LiveScanLoop(prov, reports_dir=tmp_path, max_symbols=3)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    assert events and not any(e.buy_candidate for e in events)  # 데이터 오류 → BUY_CANDIDATE 없음


# --- 타임스탬프 정규화 + 라우터 통합 ---
def test_norm_ts_handles_nanoseconds():
    from backend.app.services.order_router import _norm_ts
    out = _norm_ts("2026-06-23T14:59:59.123456789Z")
    datetime.fromisoformat(out)  # 파싱 가능해야 함(예외 없음)
    assert out.endswith("+00:00")


def test_router_uses_alpaca_quote(tmp_path):
    from backend.app.services.broker_snapshot import BrokerSnapshot
    from backend.app.services.execution_gate import OrderIntent
    from backend.app.services.order_router import RouterQuote, select_and_route

    live = Settings().live_strategy_id
    intent = OrderIntent(timestamp=NOW.isoformat(), session_id="s1", trading_mode="report_only",
                         strategy_id=live, symbol="F", side="BUY", scan_event_key="s|F",
                         mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="ok",
                         execution_gate_status="accepted_dry_run", planned_order_type="limit",
                         planned_limit_price=14.0, planned_notional_usd=50.0, planned_quantity=50.0 / 14.0)
    snap = BrokerSnapshot(timestamp=NOW.isoformat(), account_last4="••••9372", buying_power=985.97,
                          positions=[], open_orders=[], quotes=[])  # 스냅샷 호가 없음 → Alpaca 호가만
    alpaca_q = {"F": RouterQuote(symbol="F", bid=14.0, ask=14.02, last=14.05, as_of=NOW.isoformat())}
    r = select_and_route(settings=Settings(order_router_daily_max_approval_requests=1),
                         reports_dir=tmp_path, now=NOW, market_open=True, intents=[intent],
                         snapshot=snap, live_quotes=alpaca_q, send=False)
    assert r.decision == "ROUTER_SELECTED" and r.selected.ask == 14.02 and r.selected.bid == 14.0


# --- 안전 ---
def test_no_orders_no_robinhood_write_in_market_data():
    import inspect
    text = inspect.getsource(md)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text


def test_alpaca_trading_disabled_by_default():
    assert Settings().alpaca_trading_enabled is False
