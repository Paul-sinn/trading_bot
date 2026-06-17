"""Step 0 goal_planner 테스트 (TDD Red→Green).

spec: specs/goal_planner.md
- required_monthly_return: 복리 월 수익률 역산 + 엣지(분모/기간 무효, 이미 달성).
- feasibility: REALISTIC/AMBITIOUS/UNREALISTIC 임계값 경계.
- derive_settings: 모드별 역산.
- CRITICAL(ADR-003): 어떤 목표·모드에서도 max_risk_pct <= SYSTEM_MAX_RISK_PCT.
"""

import pytest

from agents.risk import RiskLimits
from algorithms.goal_planner import (
    SAFE_MAX_RISK_PCT,
    SYSTEM_MAX_RISK_PCT,
    Feasibility,
    GoalDerivedSettings,
    PlanMode,
    derive_settings,
    feasibility,
    required_monthly_return,
)


# --- required_monthly_return ---


def test_required_monthly_return_known_value():
    # 10000 → 12000, 6개월 → (1.2)**(1/6) - 1 ≈ 0.0309.
    assert required_monthly_return(10000, 12000, 6) == pytest.approx(0.0309, abs=1e-3)


def test_required_monthly_return_already_achieved_is_zero():
    assert required_monthly_return(10000, 10000, 6) == pytest.approx(0.0)


def test_required_monthly_return_target_below_current_is_negative():
    assert required_monthly_return(10000, 8000, 6) < 0


def test_required_monthly_return_invalid_current_raises():
    with pytest.raises(ValueError):
        required_monthly_return(0, 12000, 6)
    with pytest.raises(ValueError):
        required_monthly_return(-100, 12000, 6)


def test_required_monthly_return_invalid_months_raises():
    with pytest.raises(ValueError):
        required_monthly_return(10000, 12000, 0)
    with pytest.raises(ValueError):
        required_monthly_return(10000, 12000, -3)


# --- feasibility 임계값 경계 ---


def test_feasibility_realistic_boundary():
    assert feasibility(0.0) is Feasibility.REALISTIC
    assert feasibility(0.03) is Feasibility.REALISTIC


def test_feasibility_ambitious_boundary():
    assert feasibility(0.0300001) is Feasibility.AMBITIOUS
    assert feasibility(0.08) is Feasibility.AMBITIOUS


def test_feasibility_unrealistic():
    assert feasibility(0.0800001) is Feasibility.UNREALISTIC
    assert feasibility(9.0) is Feasibility.UNREALISTIC


# --- derive_settings ---


def test_derive_settings_returns_model():
    s = derive_settings(10000, 12000, 6, PlanMode.SAFE)
    assert isinstance(s, GoalDerivedSettings)
    assert isinstance(s.risk_limits, RiskLimits)
    assert 0.0 <= s.appetite <= 1.0


def test_derive_settings_extreme_goal_never_exceeds_hard_cap():
    # 가장 중요한 테스트: 1개월에 10배 — 극단적 비현실 목표.
    for mode in (PlanMode.SAFE, PlanMode.AGGRESSIVE):
        s = derive_settings(10000, 100000, 1, mode)
        assert s.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT + 1e-9
        assert s.feasibility is Feasibility.UNREALISTIC
    # SAFE는 SAFE 캡 이하.
    safe = derive_settings(10000, 100000, 1, PlanMode.SAFE)
    assert safe.risk_limits.max_risk_pct <= SAFE_MAX_RISK_PCT + 1e-9


def test_derive_settings_aggressive_ge_safe_same_goal():
    safe = derive_settings(10000, 50000, 6, PlanMode.SAFE)
    aggr = derive_settings(10000, 50000, 6, PlanMode.AGGRESSIVE)
    assert aggr.risk_limits.max_risk_pct >= safe.risk_limits.max_risk_pct
    assert aggr.appetite >= safe.appetite


def test_derive_settings_conservative_goal_low_risk():
    # 긴 기간·작은 증가 → 낮은 appetite/risk.
    conservative = derive_settings(10000, 11000, 36, PlanMode.AGGRESSIVE)
    ambitious = derive_settings(10000, 50000, 3, PlanMode.AGGRESSIVE)
    assert conservative.appetite < ambitious.appetite
    assert conservative.risk_limits.max_risk_pct < ambitious.risk_limits.max_risk_pct


def test_derive_settings_values_within_ranges():
    s = derive_settings(10000, 100000, 1, PlanMode.AGGRESSIVE)
    assert 0.0 <= s.appetite <= 1.0
    assert 0.0 < s.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT
    assert 0.0 < s.risk_limits.max_drawdown_pct <= 1.0
    assert 0.0 < s.risk_limits.max_position_pct <= 1.0
    assert s.stop_loss_atr_multiplier >= 1.5


def test_derive_settings_already_achieved_minimal():
    s = derive_settings(10000, 10000, 12, PlanMode.AGGRESSIVE)
    assert s.feasibility is Feasibility.REALISTIC
    assert s.appetite == pytest.approx(0.0)
