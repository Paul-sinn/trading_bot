"""레짐 데이터 어댑터 v1 — SPY(활성 provider) + VIX(폴백)로 레짐을 판정한다.

문제: Alpaca stocks 엔드포인트는 ^VIX를 제공하지 않아 라이브 스캔이 전 심볼 INSUFFICIENT_DATA가 된다.
해결: SPY 일봉은 활성 MarketDataProvider(보통 Alpaca)에서, VIX는 **폴백 provider(yfinance→stooq)**에서
가져온다. VIX가 없어도 스캔을 죽이지 않고 SPY-only 보수 레짐으로 진행한다.

CRITICAL: 시장데이터 전용 — 주문/Robinhood write/Alpaca 거래 없음. VIX 폴백은 **레짐 필터에만** 쓰고
종목 가격/주문 가격에는 절대 쓰지 않는다. 모든 외부 호출은 graceful(실패 → VIX 없음으로 처리).

spec: specs/real_order_v1_checklist.md §18
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from algorithms.regime import Regime, classify_regime

SPY_SYMBOL = "SPY"
VIX_SYMBOL = "^VIX"
_MA = 200

VIX_WARNING = "VIX unavailable, using SPY-only conservative regime"


class RegimeResult(BaseModel):
    """정규화된 레짐 출력. effective_regime은 pullback_entry에 넘길 Regime enum 값(또는 None)."""

    regime: str  # NORMAL_BULL|NERVOUS_BULL|BEARISH|PANIC|spy_bull_vix_unknown|spy_bear_vix_unknown|insufficient_spy
    regime_source: str  # spy+vix | spy_only | none
    effective_regime: str | None = None  # Regime.value (pullback용) — SPY 부족이면 None
    spy_close: float | None = None
    spy_200d: float | None = None
    vix_value: float | None = None
    risk_reduced: bool = False
    warnings: list[str] = Field(default_factory=list)


def _spy_metrics(spy_bars) -> tuple[pd.Series | None, float | None, float | None]:
    if spy_bars is None or "close" not in getattr(spy_bars, "columns", []) or len(spy_bars) < _MA:
        return None, None, None
    close = spy_bars["close"]
    ma = close.rolling(window=_MA, min_periods=_MA).mean().iloc[-1]
    if pd.isna(ma):
        return None, None, None
    return close, float(close.iloc[-1]), float(ma)


def _default_vix_fetch() -> float | None:
    """폴백 VIX 최신값(yfinance → stooq). 어떤 실패도 None(레짐만 영향, 스캔은 계속)."""
    try:
        import yfinance as yf

        df = yf.Ticker(VIX_SYMBOL).history(period="5d")
        if df is not None and len(df) and "Close" in df.columns:
            v = df["Close"].dropna()
            if len(v):
                return float(v.iloc[-1])
    except Exception:  # noqa: BLE001 - graceful
        pass
    try:
        import io

        import httpx

        r = httpx.get("https://stooq.com/q/d/l/", params={"s": "^vix", "i": "d"}, timeout=10.0)
        if r.status_code == 200 and r.text and "Close" in r.text:
            d = pd.read_csv(io.StringIO(r.text))
            v = d["Close"].dropna() if "Close" in d.columns else pd.Series([], dtype="float64")
            if len(v):
                return float(v.iloc[-1])
    except Exception:  # noqa: BLE001 - graceful
        pass
    return None


class RegimeDataAdapter:
    """활성 provider(SPY) + 폴백(VIX)로 레짐을 만든다. vix_fetch 주입으로 테스트(네트워크 없음)."""

    def __init__(self, provider, *, vix_fetch=None) -> None:
        self._provider = provider
        self._vix_fetch = vix_fetch  # fn() -> float|None. None이면 기본 폴백(yfinance→stooq).

    def _get_vix(self) -> float | None:
        fn = self._vix_fetch or _default_vix_fetch
        try:
            return fn()
        except Exception:  # noqa: BLE001 - graceful: VIX 폴백 오류가 스캔을 죽이지 않게
            return None

    def resolve(self, *, spy_bars=None) -> RegimeResult:
        if spy_bars is None:
            try:
                spy_bars = self._provider.get_recent_bars(SPY_SYMBOL, lookback_days=300)
            except Exception:  # noqa: BLE001 - graceful
                spy_bars = None

        close, spy_close, spy_200d = _spy_metrics(spy_bars)
        if close is None:
            # SPY가 실제로 없을 때만 레짐 불가(fail-safe).
            return RegimeResult(regime="insufficient_spy", regime_source="none", effective_regime=None,
                                warnings=["SPY 데이터 부족 — 레짐 판정 불가"])

        vix = self._get_vix()
        if vix is not None:
            reg = classify_regime(close, [vix])
            return RegimeResult(
                regime=reg.value, regime_source="spy+vix", effective_regime=reg.value,
                spy_close=spy_close, spy_200d=spy_200d, vix_value=vix,
                risk_reduced=(reg == Regime.NERVOUS_BULL),
            )

        # VIX 없음 → SPY-only 보수 레짐(스캔을 죽이지 않음).
        if spy_close is not None and spy_200d is not None and spy_close > spy_200d:
            # 상승: 진입 허용하되 보수적(NERVOUS_BULL=half size) + risk_reduced.
            return RegimeResult(
                regime="spy_bull_vix_unknown", regime_source="spy_only",
                effective_regime=Regime.NERVOUS_BULL.value, spy_close=spy_close, spy_200d=spy_200d,
                vix_value=None, risk_reduced=True, warnings=[VIX_WARNING],
            )
        # 하락: 신규 BUY 차단(BEARISH).
        return RegimeResult(
            regime="spy_bear_vix_unknown", regime_source="spy_only",
            effective_regime=Regime.BEARISH.value, spy_close=spy_close, spy_200d=spy_200d,
            vix_value=None, risk_reduced=True, warnings=[VIX_WARNING],
        )
