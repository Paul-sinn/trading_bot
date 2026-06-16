"""Step 7 sizing (Layer 3) 테스트 (TDD Red→Green).

spec: specs/sizing.md
- Kelly Criterion 변형 / ATR 스탑로스 / 투자성향 가중 / 리스크 한도 기반 수량.
- CRITICAL(ADR-003): position_size의 risk_amount는 account_equity*max_risk_pct를 절대 초과하지 않는다.
- 엣지케이스: 분모 0(win_loss_ratio=0, entry==stop), equity 부족, kelly_f 0.
"""

import math

import pytest

from algorithms.sizing import (
    PositionPlan,
    kelly_fraction,
    position_size,
    risk_appetite_weight,
    stop_loss_price,
)


# --- Kelly Criterion ---


def test_kelly_known_value_capped():
    # win_rate=0.6, ratio=2 → f = 0.6 - 0.4/2 = 0.4 → cap 0.25 적용.
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.25)


def test_kelly_below_cap_uncapped():
    # win_rate=0.5, ratio=1 → f = 0.5 - 0.5/1 = 0.0.
    assert kelly_fraction(0.5, 1.0) == pytest.approx(0.0)


def test_kelly_positive_below_cap():
    # win_rate=0.55, ratio=2, cap 높게 → f = 0.55 - 0.45/2 = 0.325.
    assert kelly_fraction(0.55, 2.0, cap=1.0) == pytest.approx(0.325)


def test_kelly_negative_clamped_to_zero():
    # 낮은 승률 → 음수 → 0.
    assert kelly_fraction(0.2, 1.0) == 0.0


def test_kelly_win_loss_ratio_zero_is_safe():
    # 분모 0 → ZeroDivision 금지, 0 반환.
    assert kelly_fraction(0.6, 0.0) == 0.0


def test_kelly_negative_ratio_is_safe():
    assert kelly_fraction(0.6, -2.0) == 0.0


def test_kelly_win_rate_one_capped():
    assert kelly_fraction(1.0, 2.0) == pytest.approx(0.25)


def test_kelly_never_exceeds_cap():
    for wr in (0.0, 0.3, 0.6, 0.9, 1.0):
        for ratio in (0.5, 1.0, 3.0, 10.0):
            f = kelly_fraction(wr, ratio)
            assert 0.0 <= f <= 0.25


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
