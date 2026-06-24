"""Alpaca 읽기 전용 연결 스모크 테스트 — 시세 전용(주문 없음).

자격/IEX 피드/일봉/최신 호가가 기존 AlpacaMarketDataProvider로 동작하는지 확인한다.
**안전 불변식**: 읽기 전용 시장데이터만 · 주문 없음 · Alpaca 거래 없음 · Robinhood write 미호출 ·
API 시크릿/전체 env 값 절대 출력 안 함.

실행:
    PYTHONPATH=. python scripts/alpaca_smoke_test.py
    PYTHONPATH=. python scripts/alpaca_smoke_test.py --scan-once

spec: specs/real_order_v1_checklist.md §17
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from backend.app.core.config import Settings
from backend.app.services.market_data import AlpacaMarketDataProvider

SMOKE_SYMBOLS = ("AAPL", "SPY", "F")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        from backend.app.services.order_router import _norm_ts

        t = datetime.fromisoformat(_norm_ts(ts))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return round((now - t).total_seconds(), 1)
    except (ValueError, TypeError):
        return None


def summarize_symbol(provider, symbol: str, *, now: datetime | None = None) -> dict:
    """심볼 1개의 안전 요약(시크릿 미포함). status: OK/WARNING/ERROR."""
    now = now or _now()
    out: dict = {
        "provider": getattr(provider, "name", "?"), "feed": getattr(provider, "feed", None),
        "symbol": symbol, "bars_count": 0, "last_bar_date": None, "last_close": None,
        "bid": None, "ask": None, "last": None, "quote_timestamp": None,
        "quote_age_seconds": None, "status": "OK",
    }
    warnings: list[str] = []

    try:
        bars = provider.get_recent_bars(symbol)
        out["bars_count"] = int(len(bars))
        if len(bars):
            idx = bars.index[-1]
            out["last_bar_date"] = str(getattr(idx, "date", lambda: idx)())
            out["last_close"] = round(float(bars["close"].iloc[-1]), 4)
        else:
            warnings.append("empty bars")
    except Exception as exc:  # noqa: BLE001 - 시크릿 미노출: 타입명만 기록
        out["status"] = "ERROR"
        out["error"] = type(exc).__name__
        return out

    try:
        q = provider.get_latest_quote(symbol)
        out["bid"], out["ask"], out["last"] = q.bid, q.ask, q.last
        out["quote_timestamp"] = q.quote_timestamp
        age = _age_seconds(q.quote_timestamp, now)
        out["quote_age_seconds"] = age
        if q.bid is None and q.ask is None and q.last is None:
            warnings.append("quote missing")
        elif age is not None and age > 86400:  # 하루 이상 → stale 경고(주말/장마감 등)
            warnings.append("quote stale")
    except Exception as exc:  # noqa: BLE001
        out["status"] = "ERROR"
        out["error"] = type(exc).__name__
        return out

    if warnings:
        out["status"] = "WARNING"
        out["warnings"] = warnings
    return out


def _fmt(d: dict) -> str:
    return (
        f"[{d['status']}] {d['symbol']} provider={d['provider']} feed={d['feed']} "
        f"bars={d['bars_count']} last_bar={d['last_bar_date']} close={d['last_close']} "
        f"bid={d['bid']} ask={d['ask']} last={d['last']} q_ts={d['quote_timestamp']} "
        f"q_age={d['quote_age_seconds']}"
        + (f" warn={d.get('warnings')}" if d.get("warnings") else "")
        + (f" err={d.get('error')}" if d.get("error") else "")
    )


def run_smoke(provider, symbols=SMOKE_SYMBOLS, *, now: datetime | None = None, print_fn=print) -> int:
    """심볼들 요약 출력. ERROR가 하나라도 있으면 1, 아니면 0."""
    code = 0
    for sym in symbols:
        d = summarize_symbol(provider, sym, now=now)
        print_fn(_fmt(d))
        if d["status"] == "ERROR":
            code = 1
    return code


def run_scan_once(settings: Settings | None = None, *, provider=None, reports_dir=None, print_fn=print) -> dict:
    """report_only 스캔 1회(라우터/승인/주문 없음). BUY_CANDIDATE/skipped/errors 카운트 반환."""
    from backend.app.services.live_scan import LiveScanLoop
    from backend.app.services.market_data import get_market_data_provider

    settings = settings or Settings()
    prov = provider or get_market_data_provider(settings)
    loop = LiveScanLoop(prov, reports_dir=reports_dir, max_symbols=settings.live_scan_max_symbols_per_cycle)
    events = loop.scan_cycle(session_id="alpaca-smoke", trading_mode="report_only")
    counts = {
        "provider": getattr(prov, "name", "?"),
        "buy_candidate": sum(1 for e in events if e.scan_status == "BUY_CANDIDATE"),
        "errors": sum(1 for e in events if e.scan_status == "ERROR"),
        "skipped": sum(1 for e in events if e.scan_status not in ("BUY_CANDIDATE", "ERROR")),
        "total": len(events),
    }
    print_fn(
        f"[scan-once] provider={counts['provider']} total={counts['total']} "
        f"buy_candidate={counts['buy_candidate']} skipped={counts['skipped']} errors={counts['errors']} "
        f"(no router, no approval, no orders)"
    )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alpaca read-only connectivity smoke test (market data only — no orders).")
    parser.add_argument("--scan-once", action="store_true", help="report_only 스캔 1회(라우터/승인/주문 없음)")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.scan_once:
        run_scan_once(settings)
        return 0

    provider = AlpacaMarketDataProvider(settings=settings)
    if not provider.provider_status().available:
        print("Alpaca not configured")  # 시크릿 미출력
        return 2
    return run_smoke(provider)


if __name__ == "__main__":
    sys.exit(main())
