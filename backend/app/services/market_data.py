"""라이브 시장데이터 어댑터 경계 — quote/bar provider 추상화.

report_only 라이브 스캔 루프가 쓰는 시장데이터 게이트웨이. **브로커·Robinhood·유료 API·LLM 없음.**
- MockMarketDataProvider: 결정론 합성 OHLCV(네트워크 없음) — 테스트/기본값.
- FreeMarketDataProvider: 기존 `agents/data_adapter.FreeDailyProvider`(yfinance) 래핑. 네트워크/
  import 실패는 graceful(provider_status.available=False, 심볼별 실패는 호출부에서 ERROR 처리).

Config `MARKET_DATA_PROVIDER`(기본 mock, 허용 mock|free). 알 수 없는 값은 fail-closed(예외).
Norgate는 리서치/섀도 전용 — 라이브 시작에 불필요.

spec: specs/live_scan.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd
from pydantic import BaseModel

from agents.data_adapter import FreeDailyProvider
from backend.app.core.config import Settings

ALLOWED_PROVIDERS = ("mock", "free", "alpaca")
SPY_SYMBOL = "SPY"
VIX_SYMBOL = "^VIX"


class MarketDataProviderNotConfigured(RuntimeError):
    """알 수 없는/미설정 provider — fail-closed."""


class AlpacaNotConfigured(RuntimeError):
    """Alpaca 키 미설정 — fail-safe(스캔이 후보를 만들지 않게 호출부가 ERROR 처리)."""


class Quote(BaseModel):
    symbol: str
    price: float
    ts: str


class MarketQuote(BaseModel):
    """정규화된 호가(라우터의 ref price/spread/freshness 용). 시세 전용 — 주문 아님."""

    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    quote_timestamp: str | None = None
    source: str = "alpaca"
    feed: str | None = None


class ProviderStatus(BaseModel):
    name: str
    available: bool
    detail: str = ""


@runtime_checkable
class MarketDataProvider(Protocol):
    """라이브 시장데이터 인터페이스(quote/bar). 구현은 mock/free로 분기."""

    name: str

    def get_quote(self, symbol: str) -> Quote: ...
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]: ...
    def get_recent_bars(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame: ...
    def provider_status(self) -> ProviderStatus: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame_from_close(close: np.ndarray) -> pd.DataFrame:
    """close 배열로 정규화된 OHLCV 프레임 생성(결정론). 인덱스는 영업일 근사."""
    n = len(close)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-19"), periods=n)
    c = pd.Series(close, index=idx, dtype="float64")
    return pd.DataFrame(
        {
            "open": c.values,
            "high": c.values,
            "low": c.values,
            "close": c.values,
            "volume": np.full(n, 1_000_000.0),
        },
        index=idx,
    )


class MockMarketDataProvider:
    """결정론 합성 시장데이터(네트워크·난수 없음).

    SPY 완만한 상승 + VIX 낮음(레짐 A) + 유니버스 심볼을 3그룹으로 구성해 의미 있는 스캔 결과를 만든다:
    그룹0 상승추세 + 마지막 눌림목 재개(BUY_CANDIDATE), 그룹1 단조 상승(눌림 없음 → SKIP),
    그룹2 하락추세(REJECT). 실데이터가 아니라 **배관 검증용** 합성값이다.
    """

    name = "mock"

    def __init__(self, *, length: int = 260) -> None:
        self._length = length
        self._frames: dict[str, pd.DataFrame] = {}

    def _build(self, symbol: str) -> pd.DataFrame:
        n = self._length
        i = np.arange(n, dtype="float64")
        if symbol == SPY_SYMBOL:
            close = 400.0 + 0.3077 * i  # 완만한 상승추세
        elif symbol == VIX_SYMBOL:
            close = np.full(n, 15.0)  # 낮은 변동성 → 레짐 A
        else:
            group = sum(ord(ch) for ch in symbol) % 3
            if group == 2:
                close = 300.0 - 0.3 * i  # 하락추세 → REJECT(trend != UP)
            else:
                close = 100.0 + 0.5 * i  # SPY보다 가파른 상승 → trend UP + 상대강도
                if group == 0:
                    # 마지막 직전 2봉을 20d선 아래로 눌렀다가 마지막 봉 재개 → BUY_CANDIDATE.
                    close[n - 3] -= 8.0
                    close[n - 2] -= 8.0
        return _frame_from_close(close)

    def get_recent_bars(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        if symbol not in self._frames:
            self._frames[symbol] = self._build(symbol)
        return self._frames[symbol].tail(lookback_days)

    def get_quote(self, symbol: str) -> Quote:
        bars = self.get_recent_bars(symbol, lookback_days=1)
        return Quote(symbol=symbol, price=float(bars["close"].iloc[-1]), ts=_now_iso())

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.get_quote(s) for s in symbols}

    def provider_status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, available=True, detail="deterministic mock")


class FreeMarketDataProvider:
    """무료 일봉(yfinance) provider 래핑. 네트워크/import 실패는 graceful(available=False)."""

    name = "free"

    def __init__(self, *, daily: FreeDailyProvider | None = None) -> None:
        self._daily = daily or FreeDailyProvider()

    def get_recent_bars(self, symbol: str, lookback_days: int = 260) -> pd.DataFrame:
        # FreeDailyProvider.get_ohlcv가 normalize까지 수행. 실패(네트워크/import)는 호출부가 ERROR로.
        df = self._daily.get_ohlcv(symbol)
        return df.tail(lookback_days)

    def get_quote(self, symbol: str) -> Quote:
        bars = self.get_recent_bars(symbol, lookback_days=5)
        return Quote(symbol=symbol, price=float(bars["close"].iloc[-1]), ts=_now_iso())

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for s in symbols:
            try:
                out[s] = self.get_quote(s)
            except Exception:  # noqa: BLE001 - graceful: 심볼 실패는 건너뜀
                continue
        return out

    def provider_status(self) -> ProviderStatus:
        # 네트워크 호출 없이 가용성 추정: fetch_fn 주입됐거나 yfinance import 가능하면 available.
        if getattr(self._daily, "_fetch_fn", None) is not None:
            return ProviderStatus(name=self.name, available=True, detail="injected fetch_fn")
        try:
            import yfinance  # noqa: F401
        except ImportError:
            return ProviderStatus(
                name=self.name, available=False, detail="yfinance 미설치(무료 provider 비활성)"
            )
        return ProviderStatus(name=self.name, available=True, detail="yfinance")


class AlpacaMarketDataProvider:
    """Alpaca 시장데이터 provider — **시세 전용(주문/거래 아님)**.

    HTTP(httpx)로 Alpaca Data API(v2)를 호출한다. 테스트는 `http_get`을 주입해 네트워크 없이 검증한다.
    키 미설정/네트워크 오류/빈 응답은 예외로 올려 호출부(스캔)가 ERROR로 처리 → BUY_CANDIDATE 안 만듦.
    APCA-API-KEY-ID/SECRET 헤더만 사용하고 키 값은 로그/페이로드에 노출하지 않는다.
    """

    name = "alpaca"

    def __init__(self, *, settings: Settings | None = None, http_get=None) -> None:
        s = settings or Settings()
        self._key = (s.alpaca_api_key_id or "").strip()
        self._secret = (s.alpaca_api_secret_key or "").strip()
        # 경로는 "/v2/..."를 붙이므로 base에 끝의 "/v2"가 있으면 제거(중복 방지). 두 형태 모두 허용.
        base = (s.alpaca_data_base_url or "https://data.alpaca.markets").rstrip("/")
        if base.endswith("/v2"):
            base = base[: -len("/v2")]
        self._base = base
        self._feed = s.alpaca_data_feed or "iex"
        self.feed = self._feed  # 공개 읽기용(스모크 요약 등). 시크릿 아님.
        self._timeframe = s.alpaca_bar_timeframe or "1Day"
        self._lookback = int(s.alpaca_lookback_days or 300)
        # http_get(url, headers, params) -> dict(JSON). None이면 실제 httpx 사용.
        self._http_get = http_get

    def _configured(self) -> bool:
        return bool(self._key and self._secret)

    def _get(self, path: str, params: dict) -> dict:
        if not self._configured():
            raise AlpacaNotConfigured("ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY 미설정")
        url = f"{self._base}{path}"
        headers = {"APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret}
        if self._http_get is not None:
            return self._http_get(url, headers, params)
        import httpx

        resp = httpx.get(url, headers=headers, params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_recent_bars(self, symbol: str, lookback_days: int | None = None) -> pd.DataFrame:
        days = int(lookback_days or self._lookback)
        # v2 bars는 start가 필요하다. 거래일 ~days개를 확보하려 달력일을 넉넉히 잡고(주말/휴일 보정),
        # 응답을 정렬한 뒤 가장 최근 days개만 tail로 취한다.
        from datetime import timedelta

        start = (datetime.now(timezone.utc) - timedelta(days=int(days * 1.6) + 7)).date().isoformat()
        data = self._get(
            f"/v2/stocks/{symbol}/bars",
            {"timeframe": self._timeframe, "start": start, "feed": self._feed,
             "adjustment": "raw", "limit": 10000},
        )
        rows = data.get("bars") or []
        if not rows:
            # 빈 bars → 빈 프레임(스캔이 INSUFFICIENT_DATA로 처리).
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        idx = pd.to_datetime([r.get("t") for r in rows], utc=True, errors="coerce")
        df = pd.DataFrame(
            {
                "open": [float(r.get("o")) for r in rows],
                "high": [float(r.get("h")) for r in rows],
                "low": [float(r.get("l")) for r in rows],
                "close": [float(r.get("c")) for r in rows],
                "volume": [float(r.get("v", 0.0)) for r in rows],
            },
            index=idx,
        ).sort_index()
        df.attrs["source"] = "alpaca"
        df.attrs["feed"] = self._feed
        return df.tail(days)

    def get_latest_quote(self, symbol: str) -> MarketQuote:
        q = (self._get(f"/v2/stocks/{symbol}/quotes/latest", {"feed": self._feed}).get("quote") or {})
        last: float | None = None
        try:  # 최신 체결가(best effort) — 실패해도 호가는 반환.
            t = self._get(f"/v2/stocks/{symbol}/trades/latest", {"feed": self._feed}).get("trade") or {}
            last = float(t["p"]) if t.get("p") is not None else None
        except Exception:  # noqa: BLE001
            last = None
        return MarketQuote(
            symbol=symbol,
            bid=float(q["bp"]) if q.get("bp") is not None else None,
            ask=float(q["ap"]) if q.get("ap") is not None else None,
            last=last,
            quote_timestamp=q.get("t"),
            source="alpaca",
            feed=self._feed,
        )

    def get_batch_latest_quotes(self, symbols: list[str]) -> dict[str, MarketQuote]:
        if not symbols:
            return {}
        data = self._get("/v2/stocks/quotes/latest", {"symbols": ",".join(symbols), "feed": self._feed})
        quotes = data.get("quotes") or {}
        out: dict[str, MarketQuote] = {}
        for sym, q in quotes.items():
            if not isinstance(q, dict):
                continue
            out[sym] = MarketQuote(
                symbol=sym,
                bid=float(q["bp"]) if q.get("bp") is not None else None,
                ask=float(q["ap"]) if q.get("ap") is not None else None,
                last=None,
                quote_timestamp=q.get("t"),
                source="alpaca",
                feed=self._feed,
            )
        return out

    # --- MarketDataProvider Protocol 호환 ---
    def get_quote(self, symbol: str) -> Quote:
        mq = self.get_latest_quote(symbol)
        price = mq.last
        if price is None and mq.bid is not None and mq.ask is not None:
            price = (mq.bid + mq.ask) / 2.0
        if price is None:
            price = float(self.get_recent_bars(symbol, lookback_days=1)["close"].iloc[-1])
        return Quote(symbol=symbol, price=price, ts=mq.quote_timestamp or _now_iso())

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for s in symbols:
            try:
                out[s] = self.get_quote(s)
            except Exception:  # noqa: BLE001 - graceful: 심볼 실패는 건너뜀
                continue
        return out

    def provider_status(self) -> ProviderStatus:
        # 네트워크 호출 없이 가용성 추정: 키가 설정돼 있으면 available.
        if not self._configured():
            return ProviderStatus(name=self.name, available=False, detail="ALPACA 키 미설정(시장데이터 비활성)")
        return ProviderStatus(name=self.name, available=True, detail=f"alpaca/{self._feed}")


def get_market_data_provider(settings: Settings | None = None) -> MarketDataProvider:
    """`MARKET_DATA_PROVIDER` 기반 provider 선택. 알 수 없는 값은 fail-closed(예외)."""
    settings = settings or Settings()
    name = (settings.market_data_provider or "").strip().lower()
    if name == "mock":
        return MockMarketDataProvider()
    if name == "free":
        return FreeMarketDataProvider()
    if name == "alpaca":
        return AlpacaMarketDataProvider(settings=settings)
    raise MarketDataProviderNotConfigured(
        f"알 수 없는 MARKET_DATA_PROVIDER={settings.market_data_provider!r} "
        f"(허용: {', '.join(ALLOWED_PROVIDERS)})"
    )
