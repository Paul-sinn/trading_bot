"""policy 순수 모델·평가자 테스트 (헌법 enforcement, spec: specs/policy.md).

부수효과 없는 순수 함수만 검증한다 — 네트워크/파일 I/O 없음. 모델은 테스트에서 직접 구성한다
(JSON 로더는 후속 step). 두 리스크 불변식(per-trade 5% / account-loss B 7%·C 10%)이 독립적으로
강제되는지, 그리고 fail-closed 동작을 본다.
"""

import math

import pytest

from algorithms.goal_planner import SYSTEM_MAX_RISK_PCT
from algorithms.policy import (
    RiskCheck,
    RiskMode,
    TierEntry,
    UniversePolicy,
    account_loss_pct,
    evaluate_risk,
    is_candidate_eligible,
    mode_allows_symbol,
    tier_status,
)


# --- 픽스처: 헌법 모드 B/C + 작은 유니버스 ---


def _mode_b() -> RiskMode:
    return RiskMode(
        name="B",
        account_loss_cap=0.07,
        allowed_tiers=("0", "1", "2", "3", "4A", "4B"),
        tier2_whitelist_only=False,
        tier2_whitelist=(),
        default=True,
    )


def _mode_c() -> RiskMode:
    return RiskMode(
        name="C",
        account_loss_cap=0.10,
        allowed_tiers=("0", "1", "2"),
        tier2_whitelist_only=True,
        tier2_whitelist=("PLTR", "COIN", "HOOD", "VRT", "CRWD", "ARM", "MU"),
        default=False,
    )


def _universe() -> UniversePolicy:
    return UniversePolicy(
        entries=(
            TierEntry("SPY", "0", ("0",), "approved", False, False),       # 컴퍼스
            TierEntry("NVDA", "1", ("1",), "approved", True, False),       # tradable leader
            TierEntry("PLTR", "2", ("2",), "approved", True, False),       # C-mode whitelist
            TierEntry("SMCI", "2", ("2",), "needs_review", True, False),   # T2 비화이트리스트
            TierEntry("SOUN", "5", ("5",), "watch", True, False),          # T5 투기
            TierEntry("SPCX", "4B", ("4B",), "needs_review", True, False), # 데이터 부족
            TierEntry("BADCO", "5", ("5",), "data_missing", True, False),  # 결측
            TierEntry("DEADX", "2", ("2",), "reject", True, False),        # 거부
        )
    )


# --- account_loss_pct ---


def test_account_loss_pct_is_weight_times_stop():
    assert account_loss_pct(0.5, 0.10) == pytest.approx(0.05)
    assert account_loss_pct(1.0, 0.07) == pytest.approx(0.07)
    assert account_loss_pct(0.0, 0.10) == 0.0


# --- evaluate_risk: 두 불변식 독립 + AND ---


def test_evaluate_risk_both_pass():
    # per_trade 0.04 <= 0.05, account_loss 0.5*0.08=0.04 <= 0.07(B)
    rc = evaluate_risk(0.04, 0.5, 0.08, _mode_b())
    assert rc.passed is True
    assert rc.per_trade_pass is True and rc.account_loss_pass is True
    assert rc.system_max_risk_pct == SYSTEM_MAX_RISK_PCT == 0.05
    assert rc.account_loss_cap == 0.07
    assert rc.account_loss_pct == pytest.approx(0.04)


def test_evaluate_risk_vetoes_when_per_trade_exceeds_5pct():
    # per_trade 0.06 > 0.05 → veto, account_loss 통과해도 전체 실패
    rc = evaluate_risk(0.06, 0.1, 0.05, _mode_c())
    assert rc.per_trade_pass is False
    assert rc.passed is False
    assert "0.05" in rc.reason or "5" in rc.reason


def test_evaluate_risk_vetoes_when_account_loss_exceeds_mode_cap():
    # per_trade 0.04 OK, account_loss 1.0*0.09=0.09 > 0.07(B) → veto
    rc = evaluate_risk(0.04, 1.0, 0.09, _mode_b())
    assert rc.per_trade_pass is True
    assert rc.account_loss_pass is False
    assert rc.passed is False


def test_c_mode_allows_higher_account_loss_than_b_but_not_per_trade():
    # account_loss 0.09: B(0.07) veto, C(0.10) 통과 — 단 per_trade는 여전히 5% 강제
    weight, stop = 1.0, 0.09
    assert evaluate_risk(0.04, weight, stop, _mode_b()).passed is False
    assert evaluate_risk(0.04, weight, stop, _mode_c()).passed is True
    # C라도 per_trade 6%는 막힌다(우회 불가)
    assert evaluate_risk(0.06, weight, stop, _mode_c()).passed is False


def test_evaluate_risk_boundary_equal_caps_pass():
    # 경계: == 캡은 통과(>만 위반)
    rc = evaluate_risk(0.05, 1.0, 0.07, _mode_b())
    assert rc.per_trade_pass is True and rc.account_loss_pass is True
    assert rc.passed is True


def test_evaluate_risk_reason_reports_all_four_values():
    rc = evaluate_risk(0.06, 1.0, 0.09, _mode_b())
    # 사용자 요구: per_trade, system_max, account_loss, cap 모두 노출
    for token in ("0.06", "0.05", "0.09", "0.07"):
        assert token in rc.reason


@pytest.mark.parametrize(
    "per_trade,weight,stop",
    [
        (-0.01, 0.5, 0.05),
        (0.04, -0.1, 0.05),
        (0.04, 0.5, -0.05),
        (float("nan"), 0.5, 0.05),
        (0.04, float("nan"), 0.05),
        (0.04, 0.5, float("nan")),
    ],
)
def test_evaluate_risk_fail_closed_on_invalid_input(per_trade, weight, stop):
    rc = evaluate_risk(per_trade, weight, stop, _mode_b())
    assert rc.passed is False


def test_risk_check_is_frozen():
    rc = evaluate_risk(0.04, 0.5, 0.08, _mode_b())
    assert isinstance(rc, RiskCheck)
    with pytest.raises(Exception):
        rc.passed = True  # type: ignore[misc]


# --- tier_status ---


def test_tier_status_lookup_and_unknown():
    u = _universe()
    assert tier_status("NVDA", u) == "approved"
    assert tier_status("SMCI", u) == "needs_review"
    assert tier_status("NOPE", u) is None


# --- is_candidate_eligible ---


def test_eligible_excludes_reject_and_data_missing_only():
    u = _universe()
    assert is_candidate_eligible("NVDA", u) is True          # approved
    assert is_candidate_eligible("SOUN", u) is True          # watch
    assert is_candidate_eligible("SMCI", u) is True          # needs_review → coarse 통과
    assert is_candidate_eligible("DEADX", u) is False        # reject
    assert is_candidate_eligible("BADCO", u) is False        # data_missing


def test_eligible_false_for_compass_and_unknown():
    u = _universe()
    assert is_candidate_eligible("SPY", u) is False          # tradable=False(컴퍼스)
    assert is_candidate_eligible("NOPE", u) is False         # 미등록 fail-closed


# --- mode_allows_symbol ---


def test_b_mode_allows_all_tradable_tiers_in_range():
    u = _universe()
    b = _mode_b()
    assert mode_allows_symbol("NVDA", b, u) is True   # T1
    assert mode_allows_symbol("PLTR", b, u) is True   # T2
    assert mode_allows_symbol("SPCX", b, u) is True   # T4B ∈ B
    assert mode_allows_symbol("SOUN", b, u) is False  # T5 ∉ B.allowed_tiers


def test_c_mode_restricts_to_tier0_2_and_whitelist():
    u = _universe()
    c = _mode_c()
    assert mode_allows_symbol("NVDA", c, u) is True    # T1 허용
    assert mode_allows_symbol("PLTR", c, u) is True    # T2 화이트리스트
    assert mode_allows_symbol("SMCI", c, u) is False   # T2 비화이트리스트 → 차단
    assert mode_allows_symbol("SPCX", c, u) is False   # T4B ∉ C
    assert mode_allows_symbol("NOPE", c, u) is False   # 미등록 fail-closed
