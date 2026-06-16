"""알고리즘 Layer 1 — 시그널 생성 (순수 함수).

가격 시계열에서 EMA 크로스(9/21) · RSI(14) · MACD 히스토그램 시그널을 계산한다.

ADR-002: 이 모듈은 부수효과 없는 순수 함수다. 파일/네트워크/DB/전역상태 접근 금지.
입력(가격 Series/DataFrame)만으로 출력(Signal)이 결정된다.

talib 의존을 피하기 위해 지표는 pandas/numpy로 직접 계산한다.

spec: specs/signals.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    """방향성 시그널."""

    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class SignalResult:
    """3개 지표 시그널과 다수결 종합."""

    ema: Signal
    rsi: Signal
    macd: Signal
    overall: Signal


# --- 지표 계산 헬퍼 (순수) ---


def _clean(prices: pd.Series) -> pd.Series:
    """NaN 제거 후 float 시리즈로 정규화한다."""
    return pd.Series(prices, dtype="float64").dropna().reset_index(drop=True)


def _ema(prices: pd.Series, span: int) -> pd.Series:
    """지수이동평균(EMA)."""
    return prices.ewm(span=span, adjust=False).mean()


def _rsi(prices: pd.Series, period: int) -> pd.Series:
    """RSI(Wilder 평활). 변동 없음(분모 0)은 RSI=50으로 처리한다."""
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 → rs = inf → rsi = 100; avg_gain==avg_loss==0 → NaN → 50.
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


def _macd_hist(prices: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    """MACD 히스토그램(MACD선 − 시그널선)."""
    macd_line = _ema(prices, fast) - _ema(prices, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


# --- 시그널 판정 (순수) ---


def ema_cross(prices: pd.Series, fast: int = 9, slow: int = 21) -> Signal:
    """단기 EMA가 장기 EMA를 상향 돌파=BULLISH, 하향=BEARISH, 그 외 NEUTRAL."""
    prices = _clean(prices)
    if len(prices) < slow + 1:
        return Signal.NEUTRAL

    ema_fast = _ema(prices, fast)
    ema_slow = _ema(prices, slow)
    diff = ema_fast - ema_slow

    prev, curr = diff.iloc[-2], diff.iloc[-1]
    if prev <= 0 < curr:
        return Signal.BULLISH
    if prev >= 0 > curr:
        return Signal.BEARISH
    return Signal.NEUTRAL


def rsi_signal(
    prices: pd.Series,
    period: int = 14,
    overbought: float = 70.0,
    oversold: float = 30.0,
) -> Signal:
    """RSI ≤ oversold → BULLISH(과매도), ≥ overbought → BEARISH(과매수), 그 외 NEUTRAL."""
    prices = _clean(prices)
    if len(prices) < period + 1:
        return Signal.NEUTRAL

    rsi = _rsi(prices, period).iloc[-1]
    if pd.isna(rsi):
        return Signal.NEUTRAL
    if rsi <= oversold:
        return Signal.BULLISH
    if rsi >= overbought:
        return Signal.BEARISH
    return Signal.NEUTRAL


def macd_signal(
    prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Signal:
    """히스토그램 음→양 전환 BULLISH, 양→음 전환 BEARISH, 그 외 NEUTRAL."""
    prices = _clean(prices)
    if len(prices) < slow + signal:
        return Signal.NEUTRAL

    hist = _macd_hist(prices, fast, slow, signal)
    prev, curr = hist.iloc[-2], hist.iloc[-1]
    if prev <= 0 < curr:
        return Signal.BULLISH
    if prev >= 0 > curr:
        return Signal.BEARISH
    return Signal.NEUTRAL


def _majority(*signals: Signal) -> Signal:
    """다수결: BULLISH 우세 → BULLISH, BEARISH 우세 → BEARISH, 동률 → NEUTRAL."""
    bull = sum(1 for s in signals if s == Signal.BULLISH)
    bear = sum(1 for s in signals if s == Signal.BEARISH)
    if bull > bear:
        return Signal.BULLISH
    if bear > bull:
        return Signal.BEARISH
    return Signal.NEUTRAL


def generate_signals(df: pd.DataFrame, price_col: str = "close") -> SignalResult:
    """df[price_col]에 3개 지표를 적용해 SignalResult를 반환한다."""
    if price_col not in df.columns:
        raise KeyError(f"price column '{price_col}' not found in DataFrame")

    prices = df[price_col]
    ema = ema_cross(prices)
    rsi = rsi_signal(prices)
    macd = macd_signal(prices)
    return SignalResult(ema=ema, rsi=rsi, macd=macd, overall=_majority(ema, rsi, macd))
