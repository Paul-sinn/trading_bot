"""알고리즘 — 레짐 필터 (SPY 200일선 + VIX → 4레짐, 순수 함수).

헌장 docs/STRATEGY.md §8: 모멘텀을 고른 순간 레짐 필터는 전략의 일부다. 지표 2개로 4레짐을 판별하고
각 레짐의 플레이북(진입 허용·사이징 배수·청산 비율)을 결정론적으로 돌려준다. 레짐은 개별 종목 신호
위에서 작동하는 마스터 스위치다 — 종목 신호가 좋아도 레짐 D면 진입 불가.

ADR-002: 부수효과 없는 순수 함수. I/O·네트워크·DB·전역상태·난수 금지. VIX/SPY는 입력으로 받는다(조회 X).
임계값·배수는 백테스트 튜닝 대상이라 파라미터로 노출한다(헌장 §8).

spec: specs/regime.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Regime(str, Enum):
    """4레짐 (헌장 §8)."""

    NORMAL_BULL = "NORMAL_BULL"    # A. 정상 강세 (SPY>200d, VIX<20)
    NERVOUS_BULL = "NERVOUS_BULL"  # B. 불안 강세 (SPY>200d, VIX 20~30)
    BEARISH = "BEARISH"            # C. 약세/하락추세 (SPY<200d)
    PANIC = "PANIC"                # D. 패닉/위기 (VIX>30, 추세 무관)


@dataclass(frozen=True)
class RegimePolicy:
    """레짐별 플레이북."""

    allow_new_entry: bool
    size_multiplier: float
    exit_fraction_on_break: float


_POLICIES: dict[Regime, RegimePolicy] = {
    Regime.NORMAL_BULL: RegimePolicy(True, 1.0, 0.0),
    Regime.NERVOUS_BULL: RegimePolicy(True, 0.5, 0.0),
    Regime.BEARISH: RegimePolicy(False, 0.0, 0.5),
    Regime.PANIC: RegimePolicy(False, 0.0, 1.0),
}


def _clean(prices: pd.Series) -> pd.Series:
    """NaN 제거 후 float 시리즈로 정규화한다."""
    return pd.Series(prices, dtype="float64").dropna().reset_index(drop=True)


def classify_regime(
    spy_prices: pd.Series,
    vix_value: float | None,
    *,
    ma_period: int = 200,
    vix_elevated: float = 20.0,
    vix_panic: float = 30.0,
) -> Regime:
    """SPY 가격·VIX로 4레짐을 판별한다 (헌장 §8). 먼저 맞는 조건을 채택.

    ① VIX None/NaN → D PANIC (fail-closed: 위험 불명 = 최대 방어)
    ② VIX > vix_panic → D PANIC (추세 무관 최우선)
    ③ SPY 데이터 부족(< ma_period) → C BEARISH (상승추세 확인 불가)
    ④ SPY < 200d MA → C BEARISH
    ⑤ SPY ≥ 200d MA & VIX < vix_elevated → A NORMAL_BULL
    ⑥ 그 외(SPY ≥ 200d MA & vix_elevated ≤ VIX ≤ vix_panic) → B NERVOUS_BULL
    """
    # fail-closed: VIX 불명(None/NaN) = 위험 불명 → 최대 방어(D).
    if vix_value is None:
        return Regime.PANIC
    vix = float(vix_value)
    if math.isnan(vix) or vix > vix_panic:
        return Regime.PANIC

    prices = _clean(spy_prices)
    if len(prices) < ma_period:
        return Regime.BEARISH

    ma = prices.rolling(window=ma_period, min_periods=ma_period).mean().iloc[-1]
    price = prices.iloc[-1]
    if pd.isna(ma) or price < ma:
        return Regime.BEARISH

    if vix < vix_elevated:
        return Regime.NORMAL_BULL
    return Regime.NERVOUS_BULL


def policy_for(regime: Regime) -> RegimePolicy:
    """레짐의 플레이북을 돌려준다 (헌장 §8)."""
    return _POLICIES[regime]
