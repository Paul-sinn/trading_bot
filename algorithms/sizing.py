"""알고리즘 Layer 3 — 포지션 사이징 (순수 함수).

Layer 1(signals)·Layer 2(filters)를 통과한 종목의 진입 수량과 스탑로스를 결정한다.
Kelly Criterion 변형 · ATR 기반 스탑로스 · 투자성향 가중 · 최대 리스크% 한도를 결합한다.

ADR-002: 이 모듈은 부수효과 없는 순수 함수다. 파일/네트워크/DB/전역상태 접근 금지.
입력만으로 출력(PositionPlan)이 결정된다.

ADR-003 (CRITICAL): position_size의 최종 리스크액은 account_equity*max_risk_pct를 절대
초과하지 않는다. 한도 초과 손실은 시스템의 가장 큰 위험이다.

talib 의존을 피하기 위해 계산은 표준 산술로 직접 한다.

spec: specs/sizing.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PositionPlan:
    """진입 계획. quantity 0이면 '진입 안 함'."""

    quantity: int
    stop_loss: float
    risk_amount: float
    kelly_fraction: float


# --- Kelly Criterion (순수) ---


def kelly_fraction(
    win_rate: float, win_loss_ratio: float, cap: float = 0.25
) -> float:
    """Kelly 분수 변형. f = win_rate - (1-win_rate)/win_loss_ratio.

    음수면 0(베팅 안 함), cap으로 상한(풀켈리 파산 위험 방지, half-Kelly 권장).
    win_loss_ratio<=0(분모 0/음수)은 베팅 근거 없음 → 0 (ZeroDivision 금지).
    반환값은 항상 [0, cap] 범위.
    """
    if win_loss_ratio <= 0:
        return 0.0

    f = win_rate - (1.0 - win_rate) / win_loss_ratio
    if f <= 0:
        return 0.0
    return min(f, cap)


# --- 스탑로스 (순수) ---


def stop_loss_price(entry: float, atr: float, multiplier: float) -> float:
    """스탑로스 = 진입가 − (ATR × 배수). 가격은 음수일 수 없으므로 하한 0."""
    return max(0.0, entry - atr * multiplier)


# --- 투자성향 가중 (순수) ---


def risk_appetite_weight(appetite: float) -> float:
    """투자성향(0.0 보수적 ~ 1.0 공격적)을 사이즈 가중치로 매핑한다.

    선형 매핑 0.5 + 0.5*appetite → 범위 (0, 1]. 공격적일수록 큰 가중치.
    Kelly 분수와 곱해도 한도를 넘기지 않도록 상한 1.0을 유지한다.
    """
    a = min(1.0, max(0.0, appetite))
    return 0.5 + 0.5 * a


# --- 최종 수량 (순수) ---


def position_size(
    account_equity: float,
    entry_price: float,
    stop_loss_price: float,
    max_risk_pct: float,
    kelly_f: float,
    appetite_weight: float,
) -> PositionPlan:
    """리스크 한도 기반 진입 수량을 계산한다.

    1주당 리스크 = entry - stop_loss. 허용 리스크액 = equity * max_risk_pct.
    수량 = floor(허용리스크 / 1주당리스크 * kelly_f * appetite_weight).
    CRITICAL(ADR-003): 최종 risk_amount는 허용 리스크액을 절대 초과하지 않는다.
    """
    per_share_risk = entry_price - stop_loss_price
    allowed_risk = account_equity * max_risk_pct

    # 엣지케이스: 분모 0/음수, 허용 리스크 없음, 베팅 안 함 → 진입 안 함.
    if (
        per_share_risk <= 0
        or allowed_risk <= 0
        or kelly_f <= 0
        or appetite_weight <= 0
    ):
        return PositionPlan(
            quantity=0,
            stop_loss=stop_loss_price,
            risk_amount=0.0,
            kelly_fraction=kelly_f,
        )

    base_qty = allowed_risk / per_share_risk
    qty = int(math.floor(base_qty * kelly_f * appetite_weight))

    # CRITICAL(ADR-003): 한도 상한. kelly_f/appetite_weight가 1을 넘는 입력에서도
    # risk_amount가 allowed_risk를 초과하지 않도록 수량을 줄인다.
    max_qty = int(math.floor(allowed_risk / per_share_risk))
    if qty > max_qty:
        qty = max_qty
    if qty < 0:
        qty = 0

    risk_amount = qty * per_share_risk
    return PositionPlan(
        quantity=qty,
        stop_loss=stop_loss_price,
        risk_amount=risk_amount,
        kelly_fraction=kelly_f,
    )
