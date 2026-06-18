"""Phase 5 step2 sizing (Layer 3) 테스트 (TDD Red→Green).

spec: specs/sizing.md  ·  상세: tasks/kelly-fix-prompt.md  ·  헌장: §6/§7/§8
- fractional Kelly(fraction×f_full, cap) — min(f,cap) 라벨버그 수정.
- effective_kelly_fraction: 콜드스타트 shrinkage(표본↑ → 켈리).
- regime_adjusted_fraction: 레짐 배수 별도 레이어(C/D=0).
- CRITICAL(ADR-003): position_size risk_amount ≤ equity*max_risk_pct (레짐배수 적용 후에도).
"""

import math

import pytest

from algorithms.regime import Regime
from algorithms.sizing import (
    PositionPlan,
    effective_kelly_fraction,
    kelly_fraction,
    position_size,
    regime_adjusted_fraction,
    risk_appetite_weight,
    stop_loss_price,
)


# --- fractional Kelly (문제 1: min(f,cap) 라벨버그 수정) ---


def test_kelly_fractional_scales_full_kelly():
    # full Kelly 0.40 → fraction 0.5 → 0.20 (cap 0.25 미만이어도 비례축소).
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.20)


def test_kelly_fractional_small_bet_also_scaled():
    # full 0.10 → 0.05. 옛 버그(min(f,cap))는 0.10을 그대로 뒀다.
    assert kelly_fraction(0.4, 2.0) == pytest.approx(0.05)


def test_kelly_fractional_tiny_bet_also_scaled():
    # full 0.04 → 0.02.
    assert kelly_fraction(0.52, 1.0) == pytest.approx(0.02)


def test_kelly_fraction_one_is_cap_only():
    # fraction=1.0 → cap-only 동작. full 0.40, cap 0.25 → 0.25.
    assert kelly_fraction(0.6, 2.0, fraction=1.0, cap=0.25) == pytest.approx(0.25)
    # full 0.325 < cap 1.0, fraction 1.0 → 0.325.
    assert kelly_fraction(0.55, 2.0, fraction=1.0, cap=1.0) == pytest.approx(0.325)


def test_kelly_zero_full_is_zero():
    # win_rate=0.5, ratio=1 → f_full = 0.0 → 0.
    assert kelly_fraction(0.5, 1.0) == pytest.approx(0.0)


def test_kelly_negative_clamped_to_zero():
    assert kelly_fraction(0.2, 1.0) == 0.0


def test_kelly_win_loss_ratio_zero_is_safe():
    assert kelly_fraction(0.6, 0.0) == 0.0


def test_kelly_negative_ratio_is_safe():
    assert kelly_fraction(0.6, -2.0) == 0.0


def test_kelly_win_rate_one_capped():
    # f_full=1.0 → fraction 0.5 → 0.5 → cap 0.25.
    assert kelly_fraction(1.0, 2.0) == pytest.approx(0.25)


def test_kelly_never_exceeds_cap():
    for wr in (0.0, 0.3, 0.6, 0.9, 1.0):
        for ratio in (0.5, 1.0, 3.0, 10.0):
            for frac in (0.25, 0.5, 1.0):
                f = kelly_fraction(wr, ratio, fraction=frac)
                assert 0.0 <= f <= 0.25


# --- effective_kelly_fraction (문제 2: 콜드스타트 shrinkage) ---


def test_effective_kelly_no_history_returns_prior():
    # sample_size=0 → w=0 → prior_fraction(기본 0.0). 콜드스타트 켈리 미사용.
    assert effective_kelly_fraction(0.6, 2.0, 0) == pytest.approx(0.0)


def test_effective_kelly_no_history_custom_prior():
    assert effective_kelly_fraction(0.6, 2.0, 0, prior_fraction=0.1) == pytest.approx(0.1)


def test_effective_kelly_large_sample_approaches_kelly():
    kelly = kelly_fraction(0.6, 2.0)  # 0.20
    eff = effective_kelly_fraction(0.6, 2.0, 100_000)
    assert eff == pytest.approx(kelly, abs=1e-3)


def test_effective_kelly_monotonic_in_sample_size():
    prev = -1.0
    for n in (0, 10, 30, 100, 1000):
        eff = effective_kelly_fraction(0.6, 2.0, n, prior_fraction=0.0)
        assert eff >= prev  # prior 0 < kelly 0.20 → 표본↑ → 단조 증가.
        prev = eff


def test_effective_kelly_within_cap():
    for n in (0, 1, 30, 500):
        eff = effective_kelly_fraction(0.9, 5.0, n, prior_fraction=0.2)
        assert 0.0 <= eff <= 0.25


def test_effective_kelly_shrinkage_midpoint():
    # n=k=30 → w=0.5 → 0.5*kelly + 0.5*prior.
    kelly = kelly_fraction(0.6, 2.0)  # 0.20
    eff = effective_kelly_fraction(0.6, 2.0, 30, prior_fraction=0.0, shrinkage_k=30)
    assert eff == pytest.approx(0.5 * kelly)


# --- regime_adjusted_fraction (문제 3: 레짐 배수 별도 레이어) ---


def test_regime_normal_bull_keeps_full_fraction():
    assert regime_adjusted_fraction(0.20, Regime.NORMAL_BULL) == pytest.approx(0.20)


def test_regime_nervous_bull_halves_fraction():
    assert regime_adjusted_fraction(0.20, Regime.NERVOUS_BULL) == pytest.approx(0.10)


def test_regime_bearish_and_panic_zero_fraction():
    assert regime_adjusted_fraction(0.20, Regime.BEARISH) == 0.0
    assert regime_adjusted_fraction(0.20, Regime.PANIC) == 0.0


def test_regime_cd_means_no_entry_via_position_size():
    # 레짐 C/D → 배수 0 → kelly_f 0 → position_size quantity 0 (진입 없음).
    for regime in (Regime.BEARISH, Regime.PANIC):
        kf = regime_adjusted_fraction(0.25, regime)
        p = position_size(100000, 100, 95, 0.02, kf, 1.0)
        assert p.quantity == 0


def test_regime_multiplier_never_exceeds_hard_cap():
    # ADR-003: 레짐배수 적용 후에도 risk_amount ≤ allowed. 배수가 캡을 올리지 못한다.
    for regime in (Regime.NORMAL_BULL, Regime.NERVOUS_BULL, Regime.BEARISH, Regime.PANIC):
        for kelly_in in (0.1, 0.25, 1.0):
            kf = regime_adjusted_fraction(kelly_in, regime)
            p = position_size(100000, 100, 95, 0.02, kf, 1.5)
            assert p.risk_amount <= 100000 * 0.02 + 1e-6


# --- 스탑로스 ---


def test_stop_loss_basic():
    # entry 100, atr 2, multiplier 1.5 → 100 - 3 = 97.
    assert stop_loss_price(100.0, 2.0, 1.5) == pytest.approx(97.0)


def test_stop_loss_floor_zero():
    # 큰 ATR로 음수가 되면 0으로 하한.
    assert stop_loss_price(10.0, 20.0, 1.5) == 0.0


# --- 투자성향 가중 ---


def test_appetite_weight_aggressive_gt_conservative():
    assert risk_appetite_weight(1.0) > risk_appetite_weight(0.0)


def test_appetite_weight_in_unit_range():
    for a in (-0.5, 0.0, 0.5, 1.0, 1.5):
        w = risk_appetite_weight(a)
        assert 0.0 < w <= 1.0


# --- position_size: 리스크 한도 (가장 중요) ---


def test_position_size_respects_risk_cap_ac():
    # AC 예시.
    p = position_size(10000, 100, 95, 0.02, 0.25, 1.0)
    assert isinstance(p, PositionPlan)
    assert p.risk_amount <= 10000 * 0.02 + 1e-6


def test_position_size_never_exceeds_cap_many_combos():
    # CRITICAL(ADR-003): 어떤 조합에서도 risk_amount는 allowed_risk를 넘지 않는다.
    for equity in (1000, 10000, 100000):
        for entry in (10.0, 100.0, 250.0):
            for stop in (entry * 0.9, entry * 0.95, entry * 0.99):
                for max_pct in (0.01, 0.02, 0.05):
                    for kelly_f in (0.1, 0.25, 1.0):
                        for weight in (0.5, 1.0, 1.5):
                            p = position_size(
                                equity, entry, stop, max_pct, kelly_f, weight
                            )
                            allowed = equity * max_pct
                            assert p.risk_amount <= allowed + 1e-6
                            assert p.quantity >= 0


def test_position_size_quantity_positive_when_room():
    p = position_size(100000, 100, 95, 0.02, 0.25, 1.0)
    assert p.quantity > 0
    assert p.stop_loss == pytest.approx(95.0)


# --- 보수적 vs 공격적 ---


def test_position_size_aggressive_ge_conservative():
    cons = position_size(100000, 100, 95, 0.02, 0.25, risk_appetite_weight(0.0))
    aggr = position_size(100000, 100, 95, 0.02, 0.25, risk_appetite_weight(1.0))
    assert aggr.quantity >= cons.quantity
    assert aggr.quantity > 0


# --- 엣지케이스: 수량 0 ---


def test_position_size_entry_equals_stop_zero_qty():
    # per_share_risk 0 → ZeroDivision 금지, 수량 0.
    p = position_size(10000, 100, 100, 0.02, 0.25, 1.0)
    assert p.quantity == 0
    assert p.risk_amount == 0.0


def test_position_size_stop_above_entry_zero_qty():
    p = position_size(10000, 100, 110, 0.02, 0.25, 1.0)
    assert p.quantity == 0
    assert p.risk_amount == 0.0


def test_position_size_insufficient_equity_zero_qty():
    # 허용 리스크가 1주 리스크에도 못 미침 → floor 0.
    p = position_size(10, 100, 95, 0.02, 0.25, 1.0)
    assert p.quantity == 0


def test_position_size_zero_equity_zero_qty():
    p = position_size(0, 100, 95, 0.02, 0.25, 1.0)
    assert p.quantity == 0
    assert p.risk_amount == 0.0


def test_position_size_kelly_zero_zero_qty():
    p = position_size(100000, 100, 95, 0.02, 0.0, 1.0)
    assert p.quantity == 0


def test_position_size_quantity_is_int():
    p = position_size(100000, 100, 95, 0.02, 0.25, 1.0)
    assert isinstance(p.quantity, int)
    assert not math.isnan(p.risk_amount)
