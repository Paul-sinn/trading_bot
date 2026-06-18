"""Phase 5 step4 — R 기반 청산 래더 테스트 (TDD Red→Green).

spec: specs/exits.md  ·  헌장: docs/STRATEGY.md §7-2/§8/§3
- 순수 함수 상태기계: (Position, Bar, regime, ...) → ExitAction.
- 모든 레벨 R 배수. ⑤ 레짐청산 % 룰(D=1.0/C=0.5). 트레일링은 올리기만(수익엔진).
- regime/sizing 호출(재구현 없음). 미래참조 없음.
"""

from algorithms.exits import Bar, ExitAction, Position, evaluate_exit
from algorithms.regime import Regime


def _pos(
    entry=100.0,
    initial_stop=95.0,
    qty=10.0,
    highest=100.0,
    current_stop=None,
    partial_taken=False,
) -> Position:
    return Position(
        entry_price=entry,
        initial_stop=initial_stop,
        qty=qty,
        highest_since_entry=highest,
        current_stop=initial_stop if current_stop is None else current_stop,
        partial_taken=partial_taken,
    )


def _bar(high, low, close) -> Bar:
    return Bar(high=high, low=low, close=close)


# R = entry - initial_stop = 100 - 95 = 5.


def test_R_property():
    assert _pos().R == 5.0


# --- ① 초기/현재 스탑 히트 → 전량 ---


def test_stop_hit_sells_full():
    pos = _pos()
    action = evaluate_exit(pos, _bar(high=99, low=94, close=96), regime=Regime.NORMAL_BULL)
    assert isinstance(action, ExitAction)
    assert action.sell_fraction == 1.0


def test_stop_not_hit_holds():
    pos = _pos()
    action = evaluate_exit(pos, _bar(high=101, low=99, close=100.5), regime=Regime.NORMAL_BULL)
    assert action.sell_fraction == 0.0


# --- ⑤ 레짐 청산: % 룰 결정론 (D=1.0, C=0.5) ---


def test_regime_panic_sells_full():
    pos = _pos()
    action = evaluate_exit(pos, _bar(high=101, low=99, close=100), regime=Regime.PANIC)
    assert action.sell_fraction == 1.0


def test_regime_bearish_sells_half():
    pos = _pos()
    action = evaluate_exit(pos, _bar(high=101, low=99, close=100), regime=Regime.BEARISH)
    assert action.sell_fraction == 0.5


def test_regime_exit_can_be_toggled_off():
    pos = _pos()
    action = evaluate_exit(
        pos, _bar(high=101, low=99, close=100), regime=Regime.BEARISH, use_regime_exit=False
    )
    assert action.sell_fraction == 0.0


# --- ② +1R 본전 스탑 상향 (완전 본전 아님 — 구조 아래) ---


def test_plus_1R_raises_stop_below_entry_not_full_breakeven():
    # highest=105 = entry + 1R. 스탑이 상향되지만 완전 본전(100)이 아니라 그 아래.
    pos = _pos(highest=105.0)
    action = evaluate_exit(pos, _bar(high=105, low=101, close=104), regime=Regime.NORMAL_BULL)
    assert action.new_stop is not None
    assert action.new_stop > pos.initial_stop      # 상향됨
    assert action.new_stop < pos.entry_price        # 완전 본전 아님(구조 아래, 헌장 경고1)
    assert action.sell_fraction == 0.0


# --- ③ +2R 부분 익절 (소량) + 스탑 상향 ---


def test_plus_2R_takes_partial_profit():
    # highest=110 = entry + 2R. 부분 익절 1/3, 러너 유지.
    pos = _pos(highest=110.0)
    action = evaluate_exit(pos, _bar(high=110, low=106, close=109), regime=Regime.NORMAL_BULL)
    assert action.sell_fraction == round(1 / 3, 10) or abs(action.sell_fraction - 1 / 3) < 1e-9
    assert 0.0 < action.sell_fraction < 1.0          # 소량(러너 유지)


def test_partial_not_repeated_when_already_taken():
    pos = _pos(highest=110.0, partial_taken=True)
    action = evaluate_exit(pos, _bar(high=110, low=106, close=109), regime=Regime.NORMAL_BULL)
    assert action.sell_fraction == 0.0               # 이미 익절 → 반복 안 함


def test_partial_can_be_toggled_off():
    pos = _pos(highest=110.0)
    action = evaluate_exit(
        pos, _bar(high=110, low=106, close=109), regime=Regime.NORMAL_BULL, use_partial=False
    )
    assert action.sell_fraction == 0.0


# --- ④ 트레일링: 올리기만 (수익 엔진) ---


def test_trailing_raises_stop_with_high():
    # 큰 수익 + ATR 제공 → 트레일 스탑 = 고점 - ATR*배수, 현재 스탑 위로 상향.
    pos = _pos(highest=130.0, current_stop=100.0, partial_taken=True)
    action = evaluate_exit(
        pos, _bar(high=130, low=126, close=129), regime=Regime.NORMAL_BULL,
        atr=2.0, trail_atr_mult=3.0,
    )
    assert action.new_stop is not None
    assert action.new_stop > 100.0                   # 상향
    assert action.new_stop == 130.0 - 2.0 * 3.0      # 고점 - ATR*배수 = 124


def test_trailing_never_lowers_stop():
    # 트레일 후보가 현재 스탑보다 낮으면 내리지 않는다(올리기만).
    pos = _pos(highest=110.0, current_stop=108.0, partial_taken=True)
    action = evaluate_exit(
        pos, _bar(high=110, low=109, close=109.5), regime=Regime.NORMAL_BULL,
        atr=5.0, trail_atr_mult=3.0,   # 후보 = 110 - 15 = 95 < 108 → 무시
    )
    assert action.new_stop is None or action.new_stop >= 108.0


# --- ⑥ 실적 전 (개별주 갭 회피) ---


def test_pre_earnings_exits():
    pos = _pos(highest=104.0)
    action = evaluate_exit(
        pos, _bar(high=104, low=101, close=103), regime=Regime.NORMAL_BULL, is_pre_earnings=True
    )
    assert action.sell_fraction == 1.0


def test_pre_earnings_can_be_toggled_off():
    pos = _pos(highest=104.0)
    action = evaluate_exit(
        pos, _bar(high=104, low=101, close=103), regime=Regime.NORMAL_BULL,
        is_pre_earnings=True, use_pre_earnings=False,
    )
    assert action.sell_fraction == 0.0


# --- ⑦ 타임 스탑 (무진전 정리) ---


def test_time_stop_exits_when_no_progress():
    # days_held 크고 highest < entry + R(무진전) → 정리.
    pos = _pos(highest=103.0)  # < 105 = entry + 1R → 무진전
    action = evaluate_exit(
        pos, _bar(high=103, low=99, close=101), regime=Regime.NORMAL_BULL,
        days_held=20, time_stop_days=10,
    )
    assert action.sell_fraction == 1.0


def test_time_stop_not_triggered_when_in_profit():
    # highest >= entry + R(진전 있음) → 타임스탑 미발동.
    pos = _pos(highest=106.0)
    action = evaluate_exit(
        pos, _bar(high=106, low=102, close=105), regime=Regime.NORMAL_BULL,
        days_held=20, time_stop_days=10,
    )
    assert action.sell_fraction < 1.0


# --- 엣지: R<=0 ---


def test_invalid_R_disables_r_levels_but_keeps_stop():
    # initial_stop >= entry → R<=0. R-레벨 규칙 비활성, 스탑 히트는 유효.
    pos = _pos(entry=100.0, initial_stop=100.0, highest=120.0)
    hold = evaluate_exit(pos, _bar(high=120, low=101, close=119), regime=Regime.NORMAL_BULL)
    assert hold.sell_fraction == 0.0   # +2R 같은 R-레벨 발동 안 함
    hit = evaluate_exit(pos, _bar(high=120, low=99, close=119), regime=Regime.NORMAL_BULL)
    assert hit.sell_fraction == 1.0    # 스탑 히트는 유효
