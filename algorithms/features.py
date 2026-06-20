"""Feature Factory — 과거 일봉에서 재사용 가능한 기술적 피처를 계산한다(순수 함수).

수익률(1/3/6m)·가중 모멘텀·상대강도·거래량비·ATR%·고점거리·추세플래그를 하나의 스냅샷으로
만든다. 트레이딩 판단을 하지 않는다 — 스캐너/디시전/사이징 동작을 바꾸지 않는다(읽기 전용).

ADR-002(단일 진실): 지표를 재구현하지 않는다. SMA는 `signals._sma`, ATR은 `filters._atr`를
재사용한다. 새 전략/시그널 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스/이벤트
API 미연결. 피처 계산 전용.

spec: specs/features.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from algorithms.filters import _atr
from algorithms.signals import _clean, _sma

# 거래일 기준 윈도우(약 21거래일 = 1개월).
WINDOW_1M = 21
WINDOW_3M = 63
WINDOW_6M = 126
RS_LOOKBACK = 63
VOL_LOOKBACK = 20
ATR_PERIOD = 14
HIGH_LOOKBACK = 252
# 가중 모멘텀 = 0.5·1m + 0.3·3m + 0.2·6m (최근일수록 가중).
MOM_WEIGHTS = (0.5, 0.3, 0.2)


class FeatureError(Exception):
    """입력 자체가 무효할 때(빈 DF / close 컬럼 부재) 발생. 데이터 '부족'은 예외가 아니다."""


@dataclass(frozen=True)
class FeatureSnapshot:
    """한 심볼의 피처 스냅샷. 계산 불가 피처는 None이고 missing_fields에 이름이 담긴다.

    판단이 아니라 측정값 모음이다 — 이 객체로 매매하지 않는다. real_orders_placed는 항상 0.
    """

    symbol: str
    as_of: str | None
    return_1m: float | None
    return_3m: float | None
    return_6m: float | None
    momentum_score: float | None
    relative_strength: float | None
    volume_ratio_20d: float | None
    atr_pct: float | None
    distance_from_high: float | None
    price_above_20ma: bool | None
    price_above_50ma: bool | None
    ma20_above_ma50: bool | None
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _ret(prices: pd.Series, window: int) -> float | None:
    """window 거래일 단순수익률. 데이터 부족(길이 ≤ window)이면 None."""
    if len(prices) <= window:
        return None
    past = prices.iloc[-1 - window]
    if past == 0 or pd.isna(past):
        return None
    return float(prices.iloc[-1] / past - 1.0)


def _benchmark_close(benchmark, price_col: str) -> pd.Series | None:
    """벤치마크를 종가 Series로 정규화. DataFrame이면 price_col 추출, 없으면 None."""
    if benchmark is None:
        return None
    if isinstance(benchmark, pd.DataFrame):
        if price_col not in benchmark.columns:
            return None
        return benchmark[price_col]
    return benchmark


def _relative_strength(asset: pd.Series, benchmark, price_col: str) -> float | None:
    """RS_LOOKBACK 기간 자산수익 − 벤치수익(초과수익). 벤치 없음/부족 → None."""
    bench = _benchmark_close(benchmark, price_col)
    if bench is None:
        return None
    bench = _clean(bench)
    asset_ret = _ret(asset, RS_LOOKBACK)
    bench_ret = _ret(bench, RS_LOOKBACK)
    if asset_ret is None or bench_ret is None:
        return None
    return float(asset_ret - bench_ret)


def _volume_ratio(df: pd.DataFrame) -> float | None:
    """최근 거래량 / 직전 VOL_LOOKBACK일 평균. volume 결측/부족/기준 0 → None."""
    if "volume" not in df.columns:
        return None
    vol = _clean(df["volume"])
    if len(vol) <= VOL_LOOKBACK:
        return None
    baseline = vol.iloc[-1 - VOL_LOOKBACK : -1].mean()
    if baseline <= 0 or pd.isna(baseline):
        return None
    return float(vol.iloc[-1] / baseline)


def _atr_pct(df: pd.DataFrame, price: float) -> float | None:
    """ATR(14) / 현재가. high/low/close 결측 또는 길이 부족 → None(filters._atr 재사용)."""
    if not {"high", "low", "close"}.issubset(df.columns):
        return None
    if len(df) < ATR_PERIOD + 1:
        return None
    atr = _atr(df, ATR_PERIOD).iloc[-1]
    if pd.isna(atr) or price <= 0:
        return None
    return float(atr / price)


def _distance_from_high(prices: pd.Series) -> float | None:
    """현재가 / 최근 HIGH_LOOKBACK일 고점 − 1 (0 이하). 비어있으면 None."""
    if len(prices) == 0:
        return None
    recent_high = prices.iloc[-HIGH_LOOKBACK:].max()
    if recent_high <= 0 or pd.isna(recent_high):
        return None
    return float(prices.iloc[-1] / recent_high - 1.0)


def _ma_flags(prices: pd.Series) -> tuple[bool | None, bool | None, bool | None]:
    """(price_above_20ma, price_above_50ma, ma20_above_ma50). 길이 부족분은 None."""
    price = prices.iloc[-1]
    ma20 = _sma(prices, 20).iloc[-1] if len(prices) >= 20 else None
    ma50 = _sma(prices, 50).iloc[-1] if len(prices) >= 50 else None
    above20 = bool(price > ma20) if ma20 is not None and not pd.isna(ma20) else None
    above50 = bool(price > ma50) if ma50 is not None and not pd.isna(ma50) else None
    if ma20 is not None and ma50 is not None and not pd.isna(ma20) and not pd.isna(ma50):
        ma20_above_ma50: bool | None = bool(ma20 > ma50)
    else:
        ma20_above_ma50 = None
    return above20, above50, ma20_above_ma50


def compute_features(
    prices: pd.DataFrame,
    *,
    symbol: str = "",
    benchmark=None,
    price_col: str = "close",
) -> FeatureSnapshot:
    """OHLCV에서 FeatureSnapshot을 계산한다(순수 — 부수효과/매매 없음).

    무효 입력(빈 DF / price_col 부재)은 FeatureError. 데이터 '부족'은 해당 피처 None +
    missing_fields로 fail-closed 처리한다(예외 아님).
    """
    if not isinstance(prices, pd.DataFrame) or price_col not in prices.columns:
        raise FeatureError(f"price column '{price_col}' 없음 — 피처 계산 불가")

    close = _clean(prices[price_col])
    if len(close) == 0:
        raise FeatureError("가격 데이터가 비어있다 — 피처 계산 불가")

    # as_of: 마지막 유효 종가의 날짜(인덱스가 날짜면 date, 아니면 위치).
    clean_idx = prices[price_col].dropna().index
    last = clean_idx[-1] if len(clean_idx) else None
    as_of = str(last.date()) if hasattr(last, "date") else (str(last) if last is not None else None)

    return_1m = _ret(close, WINDOW_1M)
    return_3m = _ret(close, WINDOW_3M)
    return_6m = _ret(close, WINDOW_6M)

    if None in (return_1m, return_3m, return_6m):
        momentum_score: float | None = None
    else:
        w1, w3, w6 = MOM_WEIGHTS
        momentum_score = float(w1 * return_1m + w3 * return_3m + w6 * return_6m)

    relative_strength = _relative_strength(close, benchmark, price_col)
    volume_ratio_20d = _volume_ratio(prices)
    atr_pct = _atr_pct(prices, float(close.iloc[-1]))
    distance_from_high = _distance_from_high(close)
    above20, above50, ma20_above_ma50 = _ma_flags(close)

    fields = {
        "return_1m": return_1m,
        "return_3m": return_3m,
        "return_6m": return_6m,
        "momentum_score": momentum_score,
        "relative_strength": relative_strength,
        "volume_ratio_20d": volume_ratio_20d,
        "atr_pct": atr_pct,
        "distance_from_high": distance_from_high,
        "price_above_20ma": above20,
        "price_above_50ma": above50,
        "ma20_above_ma50": ma20_above_ma50,
    }
    missing = tuple(name for name, value in fields.items() if value is None)

    return FeatureSnapshot(symbol=symbol, as_of=as_of, missing_fields=missing, **fields)
