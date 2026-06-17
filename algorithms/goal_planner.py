"""목표 기반 세팅 역산 (순수 함수).

"목표금액 + 목표기간(개월)"으로부터 그 목표 달성에 필요한 리스크·투자성향 세팅을 역산한다.
결정론적 계산만 한다(AI 결합은 step 1).

ADR-002: 이 모듈은 부수효과 없는 순수 함수다. 파일/네트워크/DB/Claude/전역상태 접근 금지.
입력만으로 출력(GoalDerivedSettings)이 결정된다. `import talib` 금지.

ADR-003 (CRITICAL): 어떤 모드·어떤 목표에서도 `risk_limits.max_risk_pct`는 시스템 하드캡
`SYSTEM_MAX_RISK_PCT`를 절대 초과하지 못한다. 비현실적 목표라고 해서 하드캡을 넘기면
실거래 계좌 파산 위험 — 시스템의 가장 큰 위험이다.

단위: max_risk_pct/max_drawdown_pct/max_position_pct는 분수(0.05 = 5%)이며
sizing.position_size가 분수로 소비하는 것과 일치한다.

spec: specs/goal_planner.md
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agents.risk import RiskLimits  # 단일 진실 — 재정의 금지.

# ADR-003 시스템 하드캡: 어떤 경우에도 max_risk_pct는 이 값을 넘지 못한다.
SYSTEM_MAX_RISK_PCT = 0.05
# SAFE 모드 상한(더 보수적).
SAFE_MAX_RISK_PCT = 0.02

# 실현가능성 임계값(보수적). 월 수익률 기준.
_REALISTIC_MAX = 0.03
_AMBITIOUS_MAX = 0.08
# intensity 정규화 기준: 필요 월 수익률이 _AMBITIOUS_MAX면 강도 1.0.
_INTENSITY_SCALE = _AMBITIOUS_MAX


class Feasibility(str, Enum):
    REALISTIC = "realistic"
    AMBITIOUS = "ambitious"
    UNREALISTIC = "unrealistic"


class PlanMode(str, Enum):
    SAFE = "safe"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class GoalDerivedSettings:
    """역산된 세팅. sizing/risk 레이어가 그대로 소비한다."""

    appetite: float                  # 투자성향 0.0(보수적)~1.0(공격적)
    risk_limits: RiskLimits          # agents.risk.RiskLimits 재사용
    stop_loss_atr_multiplier: float  # ATR 배수 (높을수록 넓은 스탑)
    feasibility: Feasibility
    required_monthly_return: float   # 역산된 필요 월 수익률(분수)


@dataclass(frozen=True)
class _ModeConfig:
    """모드별 상한/하한. lerp(lo, hi, intensity)로 세팅을 매핑한다."""

    appetite_cap: float
    risk_cap: float       # max_risk_pct 상한 (<= SYSTEM_MAX_RISK_PCT)
    dd_cap: float         # max_drawdown_pct 상한
    pos_cap: float        # max_position_pct 상한
    stop_cap: float       # stop_loss_atr_multiplier 상한


_MODE_CONFIG: dict[PlanMode, _ModeConfig] = {
    PlanMode.SAFE: _ModeConfig(
        appetite_cap=0.5,
        risk_cap=SAFE_MAX_RISK_PCT,
        dd_cap=0.10,
        pos_cap=0.20,
        stop_cap=2.5,
    ),
    PlanMode.AGGRESSIVE: _ModeConfig(
        appetite_cap=1.0,
        risk_cap=SYSTEM_MAX_RISK_PCT,
        dd_cap=0.20,
        pos_cap=0.40,
        stop_cap=3.0,
    ),
}


# --- 작은 순수 헬퍼 ---


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _lerp(lo: float, hi: float, t: float) -> float:
    """t∈[0,1] 선형 보간."""
    return lo + (hi - lo) * t


def _intensity(monthly_return: float) -> float:
    """필요 월 수익률 → 강도 [0,1]. 높을수록 공격적. r<=0이면 0."""
    return _clamp(monthly_return / _INTENSITY_SCALE, 0.0, 1.0)


# --- 공개 순수 함수 ---


def required_monthly_return(
    current_equity: float, target_amount: float, months: int
) -> float:
    """복리 기준 필요 월 수익률 = (target / current) ** (1/months) - 1.

    current_equity<=0(분모/복리 무효) 또는 months<=0(기간 무효)이면 ValueError.
    target<=current(이미 달성)은 예외가 아니라 공식이 자연히 <=0을 반환한다.
    """
    if current_equity <= 0:
        raise ValueError("current_equity는 0보다 커야 한다.")
    if months <= 0:
        raise ValueError("months는 0보다 커야 한다.")
    return (target_amount / current_equity) ** (1.0 / months) - 1.0


def feasibility(monthly_return: float) -> Feasibility:
    """필요 월 수익률을 실현가능성 라벨로 매핑한다(임계값은 보수적)."""
    if monthly_return <= _REALISTIC_MAX:
        return Feasibility.REALISTIC
    if monthly_return <= _AMBITIOUS_MAX:
        return Feasibility.AMBITIOUS
    return Feasibility.UNREALISTIC


def derive_settings(
    current_equity: float,
    target_amount: float,
    months: int,
    mode: PlanMode,
) -> GoalDerivedSettings:
    """목표로부터 리스크·투자성향 세팅을 역산한다.

    필요 수익률이 높을수록 appetite↑·risk%↑·스탑 넓게. SAFE는 더 낮은 캡, AGGRESSIVE는 더 허용.
    CRITICAL(ADR-003): max_risk_pct는 mode 캡과 SYSTEM_MAX_RISK_PCT를 절대 넘지 않는다.
    """
    r = required_monthly_return(current_equity, target_amount, months)
    feas = feasibility(r)
    t = _intensity(r)
    cfg = _MODE_CONFIG[mode]

    appetite = _clamp(_lerp(0.0, cfg.appetite_cap, t), 0.0, 1.0)
    max_drawdown_pct = _clamp(_lerp(0.05, cfg.dd_cap, t), 0.0, 1.0)
    max_position_pct = _clamp(_lerp(0.10, cfg.pos_cap, t), 0.0, 1.0)
    stop_loss_atr_multiplier = _lerp(1.5, cfg.stop_cap, t)

    # CRITICAL(ADR-003): 매핑 후에도 한번 더 하드캡으로 clamp. 어떤 경로로도 초과 불가.
    max_risk_pct = _lerp(0.005, cfg.risk_cap, t)
    max_risk_pct = min(max_risk_pct, cfg.risk_cap, SYSTEM_MAX_RISK_PCT)

    return GoalDerivedSettings(
        appetite=appetite,
        risk_limits=RiskLimits(
            max_risk_pct=max_risk_pct,
            max_drawdown_pct=max_drawdown_pct,
            max_position_pct=max_position_pct,
        ),
        stop_loss_atr_multiplier=stop_loss_atr_multiplier,
        feasibility=feas,
        required_monthly_return=r,
    )
