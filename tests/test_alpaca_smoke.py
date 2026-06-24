"""Alpaca 읽기 전용 스모크 테스트 스크립트 검증 — 네트워크 없이 http_get 주입, 시크릿 미출력.

검증: 시크릿 미로그 · 키 미설정 안전 실패 · bars/quote 요약 출력(시크릿 없음) · stale/missing quote 경고 ·
--scan-once가 승인/주문 안 만듦 · 스크립트에 Robinhood write 도구명 미포함.

spec: specs/real_order_v1_checklist.md §17
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.core.config import Settings
from backend.app.services.market_data import AlpacaMarketDataProvider
import scripts.alpaca_smoke_test as smoke

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)
SECRET = "SUPER-SECRET-XYZ-123"


def _settings(**kw) -> Settings:
    base = dict(market_data_provider="alpaca", alpaca_api_key_id="KEYID-ABC", alpaca_api_secret_key=SECRET,
                alpaca_data_feed="iex", alpaca_bar_timeframe="1Day", alpaca_lookback_days=300)
    base.update(kw)
    return Settings(**base)


def _bars(n: int):
    rows = [{"t": f"2024-01-{(i % 27) + 1:02d}T05:00:00Z", "o": 10.0 + i, "h": 11.0 + i, "l": 9.0 + i,
             "c": 10.0 + i, "v": 1_000_000} for i in range(n)]
    return {"bars": rows}


def _fake_http(*, bars_n=250, quote_ts="2026-06-23T14:59:59Z", quote_empty=False, trade_empty=False, raise_on=None):
    def http_get(url, headers, params):
        if raise_on and raise_on in url:
            raise RuntimeError("alpaca http error")
        if "/bars" in url:
            return _bars(bars_n)
        if "/trades/latest" in url:
            return {"trade": {} if trade_empty else {"p": 14.05, "t": quote_ts}}
        if "/quotes/latest" in url:
            return {"quote": {} if quote_empty else {"bp": 14.0, "ap": 14.02, "t": quote_ts}}
        return {}
    return http_get


def _provider(**fake_kw):
    return AlpacaMarketDataProvider(settings=_settings(), http_get=_fake_http(**fake_kw))


# --- 시크릿 미노출 ---
def test_smoke_does_not_log_secret():
    out: list[str] = []
    code = smoke.run_smoke(_provider(), now=NOW, print_fn=out.append)
    joined = "\n".join(out)
    assert SECRET not in joined and "KEYID-ABC" not in joined
    assert code == 0 and "[OK]" in joined


def test_bars_and_quote_summary_no_secret():
    d = smoke.summarize_symbol(_provider(), "AAPL", now=NOW)
    assert d["status"] == "OK" and d["bars_count"] == 250 and d["last_close"] is not None
    assert d["bid"] == 14.0 and d["ask"] == 14.02 and d["last"] == 14.05 and d["quote_timestamp"]
    assert SECRET not in smoke._fmt(d)


# --- 안전 실패 ---
def test_missing_env_fails_safely(monkeypatch):
    out: list[str] = []
    monkeypatch.setattr(smoke, "Settings", lambda: _settings(alpaca_api_key_id="", alpaca_api_secret_key=""))
    monkeypatch.setattr("builtins.print", out.append)
    code = smoke.main([])
    assert code == 2 and any("Alpaca not configured" in s for s in out)
    assert SECRET not in "\n".join(out)


def test_api_error_exits_nonzero_no_secret():
    out: list[str] = []
    code = smoke.run_smoke(_provider(raise_on="/bars"), now=NOW, print_fn=out.append)
    joined = "\n".join(out)
    assert code == 1 and "[ERROR]" in joined
    assert SECRET not in joined and "alpaca http error" not in joined  # 예외 메시지 미노출(타입명만)


# --- 경고(크래시 아님) ---
def test_empty_bars_warning():
    p = AlpacaMarketDataProvider(settings=_settings(), http_get=lambda u, h, pr: {"bars": []}
                                 if "/bars" in u else {"quote": {"bp": 14.0, "ap": 14.02, "t": "2026-06-23T14:59:59Z"}})
    d = smoke.summarize_symbol(p, "F", now=NOW)
    assert d["status"] == "WARNING" and "empty bars" in d.get("warnings", [])


def test_stale_quote_warning():
    old = (NOW - timedelta(days=3)).isoformat()
    d = smoke.summarize_symbol(_provider(quote_ts=old), "F", now=NOW)
    assert d["status"] == "WARNING" and "quote stale" in d.get("warnings", [])
    assert d["quote_age_seconds"] is not None and d["quote_age_seconds"] > 86400


def test_missing_quote_warning():
    d = smoke.summarize_symbol(_provider(quote_empty=True, trade_empty=True), "F", now=NOW)
    assert d["status"] == "WARNING" and "quote missing" in d.get("warnings", [])


# --- scan-once: 승인/주문 없음 ---
def test_scan_once_no_approvals_or_orders(tmp_path):
    out: list[str] = []
    counts = smoke.run_scan_once(_settings(), provider=_provider(bars_n=250), reports_dir=tmp_path, print_fn=out.append)
    assert counts["provider"] == "alpaca" and counts["total"] >= 1
    # 라우터/승인/주문 산출물이 만들어지지 않는다.
    assert not (tmp_path / "approval_requests.jsonl").exists()
    assert not (tmp_path / "real_execution_receipts.jsonl").exists()
    assert not (tmp_path / "order_router_decisions.jsonl").exists()
    assert "no router, no approval, no orders" in "\n".join(out)


# --- 안전: Robinhood write 미포함 ---
def test_no_robinhood_write_tool_in_script():
    from pathlib import Path
    text = Path("scripts/alpaca_smoke_test.py").read_text(encoding="utf-8")
    assert "mcp__robinhood" not in text and "place_equity_order" not in text
