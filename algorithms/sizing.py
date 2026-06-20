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

from algorithms.regime import Regime, policy_for


@dataclass(frozen=True)
class PositionPlan:
    """진입 계획. quantity 0이면 '진입 안 함'."""

    quantity: int
    stop_loss: float
    risk_amount: float
    kelly_fraction: float


# --- Kelly Criterion (순수) ---


def kelly_fraction(
    win_rate: float,
    win_loss_ratio: float,
    *,
    fraction: float = 0.5,
    cap: float = 0.25,
) -> float:
    """Fractional Kelly with a hard cap.

    f_full = win_rate - (1-win_rate)/win_loss_ratio.
    f_used = clamp(fraction × max(0, f_full), 0, cap).

    `fraction`(비례축소, 헌장 §6 MDD governor로 캘리브레이션되는 값)이 *모든* 베팅을
    비례축소한다 — 작은 베팅도. `min(f, cap)`만으로는 fractional Kelly가 아니다(라벨버그).
    `cap`은 절대 상한(풀켈리 파산 방지). `fraction=1.0`이면 cap-only 동작.
    win_loss_ratio<=0(분모 0/음수)은 베팅 근거 없음 → 0 (ZeroDivision 금지).
    반환값은 항상 [0, cap] 범위.
    """
    if win_loss_ratio <= 0:
        return 0.0

    f_full = win_rate - (1.0 - win_rate) / win_loss_ratio
    if f_full <= 0:
        return 0.0
    f_used = fraction * f_full
    if f_used <= 0:
        return 0.0
    return min(f_used, cap)


def effective_kelly_fraction(
    win_rate: float,
    win_loss_ratio: float,
    sample_size: int,
    *,
    fraction: float = 0.5,
    cap: float = 0.25,
    prior_fraction: float = 0.0,
    shrinkage_k: int = 30,
) -> float:
    """콜드스타트 shrinkage: 표본 크기로 prior → 경험적 켈리를 점진 전환한다.

    w = sample_size / (sample_size + shrinkage_k).
    f_eff = w × kelly_fraction(...) + (1-w) × prior_fraction.

    거래기록 0(sample_size<=0) → w=0 → prior_fraction(기본 0.0 = 켈리 미사용, 호출부의
    보수적 고정비율에 위임). 표본↑ → 켈리 비중 단조 증가(자동 램프업, 헌장 §7).
    켈리 입력 출처 = 백테스트 엔진(1순위) → 실거래 로그(2순위). 백테스트 미구현 현재는
    prior=0 콜드스타트 경로만 활성.
    """
    if sample_size <= 0:
        return prior_fraction

    kelly = kelly_fraction(
        win_rate, win_loss_ratio, fraction=fraction, cap=cap
    )
    w = sample_size / (sample_size + shrinkage_k)
    return w * kelly + (1.0 - w) * prior_fraction


def regime_adjusted_fraction(kelly_f: float, regime: Regime) -> float:
    """켈리 분수에 레짐 사이징 배수를 곱하는 별도 레이어 (헌장 §8).

    = max(0, kelly_f) × policy_for(regime).size_multiplier.
    A ×1.0 / B ×0.5 / C·D ×0.0 → C/D는 신규 진입 없음. 켈리 함수는 순수 유지하고
    배수는 이 레이어에서만 곱한다. 배수(≤1.0)는 ADR-003 하드캡을 *올리지* 못한다.
    """
    return max(0.0, kelly_f) * policy_for(regime).size_multiplier


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


# --- 헌법 account-risk 브리지 (순수) ---
# position_size 출력(달러)을 policy.evaluate_risk가 쓰는 분수 입력으로 변환한다. policy를 import하지
# 않는다(무순환). 무효 계좌/입력은 inf 반환 → evaluate_risk 캡 비교에서 자동 veto(fail-closed).


def per_trade_risk_pct(risk_amount: float, account_equity: float) -> float:
    """1회 매매 리스크 비율 = risk_amount / account_equity (분수). 불변식①(≤ 0.05) 입력.

    account_equity <= 0(무효 계좌) → inf(fail-closed — 0.0으로 안전한 척 하지 않는다).
    """
    if account_equity <= 0:
        return float("inf")
    return risk_amount / account_equity


def position_weight(quantity: float, entry_price: float, account_equity: float) -> float:
    """포지션 시장가치가 계좌에서 차지하는 비중 = (quantity × entry_price) / account_equity (분수).

    account_equity <= 0 → inf(fail-closed).
    """
    if account_equity <= 0:
        return float("inf")
    return (quantity * entry_price) / account_equity


def stop_loss_pct(entry_price: float, stop_loss: float) -> float:
    """스탑 도달 시 포지션 손실률 = (entry_price - stop_loss) / entry_price (분수).

    account_loss = position_weight × stop_loss_pct(불변식②) 입력. entry_price <= 0 → inf(fail-closed).
    stop >= entry(무효 스탑)면 <= 0 → 이후 evaluate_risk가 음수를 veto.
    """
    if entry_price <= 0:
        return float("inf")
    return (entry_price - stop_loss) / entry_price


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
