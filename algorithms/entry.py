"""알고리즘 — 진입 판정 (눌림목 주력 + 돌파 A/B 비교군, 순수 함수).

헌장 docs/STRATEGY.md §1: 진입은 눌림목(상승추세 중 조정 진입) 주력. 게이트(자격) = 일봉 상승추세 +
SPY 상대강도 + 레짐 A/B. 트리거(타이밍) = 추세 유지 중 단기 조정 후 재개. 돌파(Donchian)는 백테스트
A/B 비교군으로 별도 함수. 이 모듈은 진입 *판정*만 한다 — 체결(다음날 시가)·사이징은 호출부.

ADR-002: 부수효과 없는 순수 함수. I/O·네트워크·DB·전역상태·난수 금지.
미래참조 금지: 판정은 봉 종가 확정 데이터만 사용한다.
step0(signals)·step1(regime)을 재구현하지 않고 호출한다(단일 진실).

spec: specs/entry.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from algorithms.regime import Regime, policy_for
from algorithms.signals import TrendState, relative_strength, trend_state


@dataclass(frozen=True)
class EntrySignal:
    """진입 판정. enter=False면 reason에 갈린 게이트/트리거 사유."""

    enter: bool
    reason: str


def _check_gate(
    df: pd.DataFrame,
    regime: Regime,
    spy_df: pd.DataFrame,
    price_col: str,
    fast: int,
    slow: int,
    rs_lookback: int,
    rs_b_margin: float,
) -> tuple[bool, str]:
    """공통 게이트: 추세 UP AND 상대강도 AND 레짐 allow. (첫 실패 사유 반환)

    레짐 B(NERVOUS_BULL)는 고확신만 허용(헌장 §8 v2): 상대강도에 추가 마진(rs_b_margin) 요구.
    """
    if price_col not in df.columns:
        raise KeyError(f"price column '{price_col}' not found in DataFrame")
    if price_col not in spy_df.columns:
        raise KeyError(f"price column '{price_col}' not found in spy_df")

    if trend_state(df[price_col], fast=fast, slow=slow) != TrendState.UP:
        return False, "게이트 실패: 일봉 상승추세 아님(trend != UP)"

    # B 고확신: 상대강도 마진을 요구(상위). A 등은 마진 0(기본 상대강도).
    margin = rs_b_margin if regime == Regime.NERVOUS_BULL else 0.0
    rs = relative_strength(
        df[price_col], spy_df[price_col], lookback=rs_lookback, min_outperformance=margin
    )
    if rs is not True:
        if regime == Regime.NERVOUS_BULL:
            return False, "게이트 실패: 레짐 B 고확신 미달(상대강도 마진 부족)"
        return False, "게이트 실패: SPY 대비 상대강도 미충족"

    if not policy_for(regime).allow_new_entry:
        return False, f"게이트 실패: 레짐 {regime.value} 신규 진입 불가"

    return True, "게이트 통과(추세 UP + 상대강도 + 레짐 allow)"


def pullback_entry(
    df: pd.DataFrame,
    *,
    regime: Regime,
    spy_df: pd.DataFrame,
    price_col: str = "close",
    fast: int = 50,
    slow: int = 200,
    rs_lookback: int = 63,
    rs_b_margin: float = 0.05,
    short_ma: int = 20,
    window: int = 5,
    touch_tol: float = 0.0,
) -> EntrySignal:
    """눌림목 진입 판정 (헌장 §1 주력).

    게이트 통과 후 트리거: 최근 window 봉 내 직전 봉이 short_ma선 근처/아래로 눌렸다가
    (close <= ma×(1+touch_tol)) 마지막 봉이 재개(close[-1]>close[-2] 그리고 close[-1]>=ma[-1]).
    """
    gate_ok, reason = _check_gate(
        df, regime, spy_df, price_col, fast, slow, rs_lookback, rs_b_margin
    )
    if not gate_ok:
        return EntrySignal(enter=False, reason=reason)

    close = pd.Series(df[price_col], dtype="float64").reset_index(drop=True)
    if len(close) < max(short_ma, window) + 1:
        return EntrySignal(enter=False, reason="트리거 불가: 데이터 부족")

    ma = close.rolling(window=short_ma, min_periods=short_ma).mean()
    recent_close = close.iloc[-window:]
    recent_ma = ma.iloc[-window:]
    if recent_ma.isna().any():
        return EntrySignal(enter=False, reason="트리거 불가: 이동평균 워밍업 전")

    # 조정: 윈도우 내 직전 봉들 중 하나가 20d선 근처/아래로 눌림.
    pulled = bool(
        (recent_close.iloc[:-1] <= recent_ma.iloc[:-1] * (1.0 + touch_tol)).any()
    )
    # 재개: 마지막 봉이 상승 전환 + 20d선 위로 회복.
    resumed = bool(
        close.iloc[-1] > close.iloc[-2] and close.iloc[-1] >= ma.iloc[-1]
    )

    if pulled and resumed:
        return EntrySignal(enter=True, reason="진입: 게이트 통과 + 눌림목 재개")
    if not pulled:
        return EntrySignal(enter=False, reason="트리거 미충족: 눌림 없음(눌림 대기)")
    return EntrySignal(enter=False, reason="트리거 미충족: 재개 신호 없음")


def breakout_entry(
    df: pd.DataFrame,
    *,
    regime: Regime,
    spy_df: pd.DataFrame,
    price_col: str = "close",
    fast: int = 50,
    slow: int = 200,
    rs_lookback: int = 63,
    rs_b_margin: float = 0.05,
    lookback: int = 20,
) -> EntrySignal:
    """돌파 진입 판정 (A/B 비교군 — 헌장 §1: 눌림목과 백테스트 비교).

    게이트 통과 후 트리거: 최신 종가가 직전 lookback 봉의 최고 종가(Donchian 상단)를 초과.
    """
    gate_ok, reason = _check_gate(
        df, regime, spy_df, price_col, fast, slow, rs_lookback, rs_b_margin
    )
    if not gate_ok:
        return EntrySignal(enter=False, reason=reason)

    close = pd.Series(df[price_col], dtype="float64").reset_index(drop=True)
    if len(close) < lookback + 1:
        return EntrySignal(enter=False, reason="트리거 불가: 데이터 부족")

    prior_high = close.iloc[-1 - lookback:-1].max()
    if close.iloc[-1] > prior_high:
        return EntrySignal(enter=True, reason="진입: 게이트 통과 + 20일 신고가 돌파")
    return EntrySignal(enter=False, reason="트리거 미충족: 신고가 돌파 아님")
