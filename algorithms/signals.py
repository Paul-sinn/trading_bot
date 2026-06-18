"""알고리즘 Layer 1 — 시계열 모멘텀·추세 신호 (순수 함수).

헌장 docs/STRATEGY.md §1: 매수 방향(트리거)은 **일봉 중기 추세(시계열 모멘텀)** 단일 책임이다.
기존 "EMA(9/21)·RSI(14)·MACD 다수결"은 폐기했다 — 추세추종(방향)과 평균회귀(타이밍)를 섞으면
알파가 상쇄된다(헌장 §1). RSI는 독립 매수신호가 아니라 "상승추세 안의 눌림 타이밍"으로 강등되며,
이 레이어는 RSI **원시값만** 제공한다(실제 소비는 step3 entry).

ADR-002: 부수효과 없는 순수 함수. 파일/네트워크/DB/전역상태/난수 접근 금지.
ADR-008: talib 의존 금지 — 지표는 pandas/numpy로 직접 계산한다.

spec: specs/signals.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    """방향성 시그널 (다운스트림 scanner/decision 호환)."""

    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"


class TrendState(str, Enum):
    """일봉 중기 추세 판정."""

    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class SignalResult:
    """추세 기반 종합 신호.

    overall은 trend에서 결정론적으로 파생된다(다수결·RSI 영향 없음).
    rsi는 step3 눌림 타이밍용 원시값일 뿐, 매수신호가 아니다.
    """

    trend: TrendState
    overall: Signal
    relative_strength: bool | None = None
    rsi: float | None = None


_TREND_TO_SIGNAL = {
    TrendState.UP: Signal.BULLISH,
    TrendState.DOWN: Signal.BEARISH,
    TrendState.NEUTRAL: Signal.NEUTRAL,
}


# --- 지표 계산 헬퍼 (순수) ---


def _clean(prices: pd.Series) -> pd.Series:
    """NaN 제거 후 float 시리즈로 정규화한다."""
    return pd.Series(prices, dtype="float64").dropna().reset_index(drop=True)


def _sma(prices: pd.Series, window: int) -> pd.Series:
    """단순이동평균(SMA). 부분 윈도우(window 미만)는 NaN."""
    return prices.rolling(window=window, min_periods=window).mean()


def _rsi(prices: pd.Series, period: int) -> pd.Series:
    """RSI(Wilder 평활). 변동 없음(분모 0)은 RSI=50으로 처리한다."""
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 → rs = inf → rsi = 100; avg_gain == avg_loss == 0 → NaN → 50.
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return rsi


# --- 신호 판정 (순수) ---


def trend_state(prices: pd.Series, fast: int = 50, slow: int = 200) -> TrendState:
    """일봉 중기 추세를 MA 정렬로 판정한다 (헌장 §1: 종가 vs 50d/200d).

    UP   : 최신 종가 > MA(fast) > MA(slow)  (상승 정렬)
    DOWN : 최신 종가 < MA(fast) < MA(slow)  (하락 정렬)
    그 외 / 워밍업 전(데이터 < slow) → NEUTRAL (안전 기본값).
    """
    prices = _clean(prices)
    if len(prices) < slow:
        return TrendState.NEUTRAL

    ma_fast = _sma(prices, fast).iloc[-1]
    ma_slow = _sma(prices, slow).iloc[-1]
    price = prices.iloc[-1]
    if pd.isna(ma_fast) or pd.isna(ma_slow):
        return TrendState.NEUTRAL

    if price > ma_fast > ma_slow:
        return TrendState.UP
    if price < ma_fast < ma_slow:
        return TrendState.DOWN
    return TrendState.NEUTRAL


def relative_strength(
    asset_prices: pd.Series,
    benchmark_prices: pd.Series,
    lookback: int = 63,
    *,
    min_outperformance: float = 0.0,
) -> bool | None:
    """SPY(벤치마크) 대비 상대강도 (헌장 §1 보조 필터: 시장보다 강한 종목).

    lookback 기간 자산 수익률 > 벤치마크 수익률 + min_outperformance → True. 약하면 False.
    min_outperformance(기본 0.0)는 "상대강도 상위" 마진 — step3 B레짐 고확신 게이트가 양수로 쓴다(헌장 §8).
    둘 중 하나라도 워밍업 전(길이 ≤ lookback)이면 None(판정 불가).
    호출자가 두 시리즈를 같은 시점 말단으로 정렬해 전달한다고 가정한다.
    """
    asset = _clean(asset_prices)
    bench = _clean(benchmark_prices)
    if len(asset) <= lookback or len(bench) <= lookback:
        return None

    asset_ret = asset.iloc[-1] / asset.iloc[-1 - lookback] - 1.0
    bench_ret = bench.iloc[-1] / bench.iloc[-1 - lookback] - 1.0
    return bool(asset_ret > bench_ret + min_outperformance)


def rsi_value(prices: pd.Series, period: int = 14) -> float | None:
    """최신 RSI 원시값 (Signal 아님 — step3 눌림 타이밍 전용).

    데이터 길이 < period+1 → None. 변동 0이면 50.0 (ZeroDivision 금지).
    """
    prices = _clean(prices)
    if len(prices) < period + 1:
        return None
    rsi = _rsi(prices, period).iloc[-1]
    if pd.isna(rsi):
        return None
    return float(rsi)


def _benchmark_series(
    benchmark: pd.Series | pd.DataFrame | None, price_col: str
) -> pd.Series | None:
    """벤치마크를 Series로 정규화 (DataFrame이면 price_col 추출)."""
    if benchmark is None:
        return None
    if isinstance(benchmark, pd.DataFrame):
        if price_col not in benchmark.columns:
            raise KeyError(f"benchmark price column '{price_col}' not found")
        return benchmark[price_col]
    return benchmark


def generate_signals(
    df: pd.DataFrame,
    benchmark: pd.Series | pd.DataFrame | None = None,
    price_col: str = "close",
    *,
    fast: int = 50,
    slow: int = 200,
    rsi_period: int = 14,
    rs_lookback: int = 63,
) -> SignalResult:
    """df[price_col]에서 추세 기반 SignalResult를 만든다.

    overall은 trend에서 결정론적으로 파생된다(다수결·RSI 미사용 — 헌장 §1).
    benchmark가 주어지면 상대강도를 계산한다.
    """
    if price_col not in df.columns:
        raise KeyError(f"price column '{price_col}' not found in DataFrame")

    prices = df[price_col]
    trend = trend_state(prices, fast=fast, slow=slow)

    rs: bool | None = None
    bench = _benchmark_series(benchmark, price_col)
    if bench is not None:
        rs = relative_strength(prices, bench, lookback=rs_lookback)

    return SignalResult(
        trend=trend,
        overall=_TREND_TO_SIGNAL[trend],
        relative_strength=rs,
        rsi=rsi_value(prices, rsi_period),
    )
