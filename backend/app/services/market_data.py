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

ALLOWED_PROVIDERS = ("mock", "free")
SPY_SYMBOL = "SPY"
VIX_SYMBOL = "^VIX"


class MarketDataProviderNotConfigured(RuntimeError):
    """알 수 없는/미설정 provider — fail-closed."""


class Quote(BaseModel):
    symbol: str
    price: float
    ts: str


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


def get_market_data_provider(settings: Settings | None = None) -> MarketDataProvider:
    """`MARKET_DATA_PROVIDER` 기반 provider 선택. 알 수 없는 값은 fail-closed(예외)."""
    settings = settings or Settings()
    name = (settings.market_data_provider or "").strip().lower()
    if name == "mock":
        return MockMarketDataProvider()
    if name == "free":
        return FreeMarketDataProvider()
    raise MarketDataProviderNotConfigured(
        f"알 수 없는 MARKET_DATA_PROVIDER={settings.market_data_provider!r} "
        f"(허용: {', '.join(ALLOWED_PROVIDERS)})"
    )
