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
    # v2(헌장 §8): C 강제청산 제거(0.5→0.0). 신규만 막고 기존은 개별 스탑/트레일로 관리(churn 제거).
    Regime.BEARISH: RegimePolicy(False, 0.0, 0.0),
    Regime.PANIC: RegimePolicy(False, 0.0, 1.0),
}


def _clean(prices: pd.Series) -> pd.Series:
    """NaN 제거 후 float 시리즈로 정규화한다."""
    return pd.Series(prices, dtype="float64").dropna().reset_index(drop=True)


def _vix_values(vix_recent: object) -> list[float]:
    """vix_recent(스칼라 또는 시리즈)를 NaN 제거한 float 리스트로 정규화한다."""
    if vix_recent is None:
        return []
    if isinstance(vix_recent, (int, float)):
        v = float(vix_recent)
        return [] if math.isnan(v) else [v]
    series = pd.Series(vix_recent, dtype="float64").dropna()
    return [float(x) for x in series.to_list()]


def classify_regime(
    spy_prices: pd.Series,
    vix_recent: object,
    *,
    ma_period: int = 200,
    vix_elevated: float = 20.0,
    vix_panic: float = 30.0,
    vix_extreme: float = 35.0,
    panic_consecutive_days: int = 2,
) -> Regime:
    """SPY 가격·VIX로 4레짐을 판별한다 (헌장 §8 v2). 먼저 맞는 조건을 채택.

    vix_recent: 스칼라 또는 최근 VIX 시리즈(D 히스테리시스 위해 최소 2일 권장).
    ① VIX 불명(None/NaN/빈 시리즈) → D PANIC (fail-closed)
    ② D 확정: 최신 VIX > vix_extreme(35) OR 최근 panic_consecutive_days(2)일 연속 > vix_panic(30)
    ③ SPY 데이터 부족(< ma_period) → C BEARISH (상승추세 확인 불가)
    ④ SPY < 200d MA → C BEARISH
    ⑤ SPY ≥ 200d MA & 최신 VIX < vix_elevated → A NORMAL_BULL
    ⑥ 그 외(SPY ≥ 200d MA & 최신 VIX ≥ vix_elevated) → B NERVOUS_BULL
    """
    vix_vals = _vix_values(vix_recent)
    if not vix_vals:
        return Regime.PANIC  # fail-closed: VIX 불명 = 위험 불명 → 최대 방어
    latest = vix_vals[-1]

    # D 확정조건(히스테리시스 — 단발 VIX 스파이크로 패닉청산 방지).
    extreme = latest > vix_extreme
    recent = vix_vals[-panic_consecutive_days:]
    consecutive = len(recent) >= panic_consecutive_days and all(
        v > vix_panic for v in recent
    )
    if extreme or consecutive:
        return Regime.PANIC

    prices = _clean(spy_prices)
    if len(prices) < ma_period:
        return Regime.BEARISH

    ma = prices.rolling(window=ma_period, min_periods=ma_period).mean().iloc[-1]
    price = prices.iloc[-1]
    if pd.isna(ma) or price < ma:
        return Regime.BEARISH

    if latest < vix_elevated:
        return Regime.NORMAL_BULL
    return Regime.NERVOUS_BULL


def policy_for(regime: Regime) -> RegimePolicy:
    """레짐의 플레이북을 돌려준다 (헌장 §8)."""
    return _POLICIES[regime]
