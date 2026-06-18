"""알고리즘 — R 기반 스케일아웃 청산 래더 (순수 함수 상태기계).

헌장 docs/STRATEGY.md §7-2: 청산이 진입보다 P&L을 더 좌우한다. "손실은 짧게, 이익은 길게"
(모멘텀=양의 스큐 → 빡빡한 목표가로 승자 조기 종료 금지). 모든 레벨은 R(초기 리스크)의 배수.
포지션 상태 + 신규 바 → 청산 액션(전량/부분/유지 + 스탑 상향)을 결정한다.

ADR-002: 부수효과 없는 순수 함수. I/O·네트워크·DB·전역상태·난수 금지. 미래참조 금지(현재 바까지만).
⑤ 레짐 청산은 step1 policy_for(regime).exit_fraction을 사용(재량 금지, % 룰 결정론). regime 재구현 금지.
레이어(②③④⑤⑥⑦)는 토글 가능 — step5 백테스트 A/B 검증용.

spec: specs/exits.md
"""

from __future__ import annotations

from dataclasses import dataclass

from algorithms.regime import Regime, policy_for


@dataclass(frozen=True)
class Bar:
    """확정된 일봉(현재 바)."""

    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Position:
    """포지션 상태. 엔진이 ExitAction을 적용해 다음 Position을 조립한다."""

    entry_price: float
    initial_stop: float
    qty: float
    highest_since_entry: float
    current_stop: float
    partial_taken: bool

    @property
    def R(self) -> float:
        """1주당 초기 리스크. <=0이면 R-레벨 규칙 비활성."""
        return self.entry_price - self.initial_stop


@dataclass(frozen=True)
class ExitAction:
    """청산 액션. sell_fraction은 현재 qty 대비 비율 [0,1], 0이면 유지."""

    sell_fraction: float
    new_stop: float | None
    reason: str


def _raised_stop(
    position: Position,
    highest: float,
    atr: float | None,
    *,
    use_breakeven: bool,
    use_trailing: bool,
    breakeven_R: float,
    breakeven_buffer: float,
    trail_atr_mult: float,
) -> float | None:
    """②본전 + ④트레일링 스탑 상향 후보. '올리기만' — 현재 스탑보다 높을 때만 반환."""
    candidate = position.current_stop
    R = position.R

    # ② +breakeven_R 도달 → 본전 근처로 상향(완전 본전 아님, 구조 아래 = entry - buffer·R).
    if use_breakeven and R > 0 and highest >= position.entry_price + breakeven_R * R:
        candidate = max(candidate, position.entry_price - breakeven_buffer * R)

    # ④ 트레일링(수익 엔진): 고점 - ATR×배수. 고점 따라 올라감.
    if use_trailing and atr is not None and highest > position.entry_price:
        candidate = max(candidate, highest - atr * trail_atr_mult)

    return candidate if candidate > position.current_stop else None


def evaluate_exit(
    position: Position,
    bar: Bar,
    *,
    regime: Regime,
    is_pre_earnings: bool = False,
    days_held: int = 0,
    atr: float | None = None,
    breakeven_R: float = 1.0,
    breakeven_buffer: float = 0.2,
    partial_take_R: float = 2.0,
    partial_fraction: float = 1.0 / 3.0,
    trail_atr_mult: float = 4.0,  # v2(헌장 §8): 트레일 더 넓게(승자 태우기, 3.0→4.0)
    time_stop_days: int = 15,  # v2: 무진전 정리 늦춤(10→15)
    pre_earnings_fraction: float = 1.0,
    use_breakeven: bool = True,
    use_partial: bool = True,
    use_trailing: bool = True,
    use_regime_exit: bool = True,
    use_time_stop: bool = True,
    use_pre_earnings: bool = True,
) -> ExitAction:
    """청산 래더를 평가해 단일 ExitAction을 반환한다(안전 우선 우선순위)."""
    highest = max(position.highest_since_entry, bar.high)
    R = position.R

    # ① 스탑 히트 → 전량 (자본 보호 최우선).
    if bar.low <= position.current_stop:
        return ExitAction(1.0, None, "① 스탑 히트 → 전량 청산")

    # ⑤D 레짐 패닉 → 전량.
    regime_exit = policy_for(regime).exit_fraction_on_break if use_regime_exit else 0.0
    if regime_exit >= 1.0:
        return ExitAction(1.0, None, f"⑤ 레짐 {regime.value} 패닉 → 전량 청산")

    # ⑥ 실적 전(개별주 갭 회피) → 축소/청산.
    if use_pre_earnings and is_pre_earnings:
        return ExitAction(pre_earnings_fraction, None, "⑥ 실적 전 갭 회피 청산")

    # ⑦ 타임 스탑: 무진전(highest < entry + R) & 보유일 초과 → 정리.
    if (
        use_time_stop
        and R > 0
        and days_held >= time_stop_days
        and highest < position.entry_price + R
    ):
        return ExitAction(1.0, None, "⑦ 타임 스탑: 무진전 정리")

    # ⑤C 레짐 약세 → 부분 청산(% 룰 결정론).
    if 0.0 < regime_exit < 1.0:
        return ExitAction(regime_exit, None, f"⑤ 레짐 {regime.value} 약세 → 부분 청산")

    new_stop = _raised_stop(
        position,
        highest,
        atr,
        use_breakeven=use_breakeven,
        use_trailing=use_trailing,
        breakeven_R=breakeven_R,
        breakeven_buffer=breakeven_buffer,
        trail_atr_mult=trail_atr_mult,
    )

    # ③ +partial_take_R 부분 익절(소량, 러너 유지) + 동시 스탑 상향.
    if (
        use_partial
        and not position.partial_taken
        and R > 0
        and highest >= position.entry_price + partial_take_R * R
    ):
        return ExitAction(partial_fraction, new_stop, "③ 부분 익절 + 스탑 상향")

    # ②④ 스탑 상향만.
    if new_stop is not None:
        return ExitAction(0.0, new_stop, "②④ 스탑 상향(본전/트레일링)")

    return ExitAction(0.0, None, "유지")
