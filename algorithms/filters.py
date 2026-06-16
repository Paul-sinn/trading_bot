"""알고리즘 Layer 2 — 필터링 (순수 함수 + 주입형 센티먼트).

Layer 1(signals)을 통과한 후보를 거래량 급등 · ATR 변동성 · 뉴스 센티먼트 · VIX로 거른다.

ADR-002: 결정론적 필터(volume/atr/vix)는 부수효과 없는 순수 함수다.
파일/네트워크/DB/전역상태 접근 금지. 입력만으로 출력(bool)이 결정된다.

ADR-005: 외부 의존(뉴스 센티먼트 = Claude)은 provider 주입으로 격리한다. 알고리즘은
provider 인터페이스에만 의존하고 실제 Claude 호출 로직을 품지 않는다.

talib 의존을 피하기 위해 ATR/거래량 지표는 pandas/numpy로 직접 계산한다.

spec: specs/filters.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True)
class FilterResult:
    """4개 필터 통과여부와 AND 종합."""

    volume: bool
    atr: bool
    sentiment: bool
    vix: bool
    passed: bool


# --- 센티먼트 provider (외부 의존 주입) ---


@runtime_checkable
class SentimentProvider(Protocol):
    """뉴스 센티먼트 조회 인터페이스. 구현은 Mock/Claude로 분기."""

    def is_positive(self, symbol: str) -> bool: ...


class MockSentimentProvider:
    """결정론적 센티먼트 provider (TDD용).

    생성 시 받은 심볼별 매핑과 기본값으로 응답한다. 외부 호출·난수 없음.
    """

    def __init__(
        self, mapping: dict[str, bool] | None = None, default: bool = True
    ) -> None:
        self._mapping = dict(mapping or {})
        self._default = default

    def is_positive(self, symbol: str) -> bool:
        return self._mapping.get(symbol, self._default)


class ClaudeSentimentProvider:
    """실제 Claude 뉴스 센티먼트 연동 골격.

    이 step에서는 로직을 채우지 않는다 (키/연동은 후속 phase).
    키가 없으면 명확한 예외, 있어도 실호출하지 않고 NotImplementedError.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def is_positive(self, symbol: str) -> bool:
        if not self._api_key:
            raise ValueError(
                "Claude API 키가 없다. 센티먼트 조회 불가 (후속 phase에서 연동)."
            )
        raise NotImplementedError(
            "Claude 센티먼트 연동은 후속 phase에서 구현한다. "
            "현재는 키가 있어도 실호출하지 않는다."
        )


# --- 계산 헬퍼 (순수) ---


def _clean(series: pd.Series) -> pd.Series:
    """NaN 제거 후 float 시리즈로 정규화한다."""
    return pd.Series(series, dtype="float64").dropna().reset_index(drop=True)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR(평균 True Range). high/low/close로 계산, Wilder 평활."""
    high = pd.Series(df["high"], dtype="float64")
    low = pd.Series(df["low"], dtype="float64")
    close = pd.Series(df["close"], dtype="float64")

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# --- 필터 (결정론적 순수 함수) ---


def volume_spike(
    volume: pd.Series, lookback: int = 20, multiplier: float = 1.5
) -> bool:
    """마지막 거래량이 직전 lookback 이동평균 × multiplier 초과면 True."""
    volume = _clean(volume)
    if len(volume) < lookback:
        return False

    latest = volume.iloc[-1]
    baseline = volume.iloc[-lookback:-1].mean()
    if baseline <= 0:
        return False
    return bool(latest > baseline * multiplier)


def atr_filter(df: pd.DataFrame, period: int = 14, max_atr_pct: float = 0.08) -> bool:
    """ATR/현재가 비율이 max_atr_pct 이하(과도한 변동성 회피)면 통과 True."""
    for col in ("high", "low", "close"):
        if col not in df.columns:
            raise KeyError(f"column '{col}' not found in DataFrame")

    if len(df) < period + 1:
        return False

    atr = _atr(df, period).iloc[-1]
    price = pd.Series(df["close"], dtype="float64").iloc[-1]
    if pd.isna(atr) or pd.isna(price) or price <= 0:
        return False
    return bool((atr / price) <= max_atr_pct)


def sentiment_filter(symbol: str, provider: SentimentProvider) -> bool:
    """provider.is_positive(symbol)를 통과여부로 사용. provider 미주입 시 ValueError."""
    if provider is None:
        raise ValueError("sentiment_filter requires a SentimentProvider (provider=None)")
    return bool(provider.is_positive(symbol))


def vix_filter(vix_value: float | None, max_vix: float = 30.0) -> bool:
    """VIX ≤ max_vix이면 통과 True. None/NaN은 판단 불가로 False."""
    if vix_value is None:
        return False
    try:
        if math.isnan(float(vix_value)):
            return False
    except (TypeError, ValueError):
        return False
    return bool(float(vix_value) <= max_vix)


def apply_filters(
    df: pd.DataFrame,
    symbol: str,
    vix: float | None,
    sentiment_provider: SentimentProvider,
    *,
    lookback: int = 20,
    multiplier: float = 1.5,
    period: int = 14,
    max_atr_pct: float = 0.08,
    max_vix: float = 30.0,
) -> FilterResult:
    """4개 필터를 적용하고 AND 결합한 FilterResult를 반환한다.

    하나라도 실패하면 passed=False. df에는 volume/high/low/close 컬럼이 있어야 한다.
    """
    if "volume" not in df.columns:
        raise KeyError("column 'volume' not found in DataFrame")

    volume_ok = volume_spike(df["volume"], lookback=lookback, multiplier=multiplier)
    atr_ok = atr_filter(df, period=period, max_atr_pct=max_atr_pct)
    sentiment_ok = sentiment_filter(symbol, sentiment_provider)
    vix_ok = vix_filter(vix, max_vix=max_vix)

    return FilterResult(
        volume=volume_ok,
        atr=atr_ok,
        sentiment=sentiment_ok,
        vix=vix_ok,
        passed=volume_ok and atr_ok and sentiment_ok and vix_ok,
    )
