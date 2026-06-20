"""시뮬 포지션 청산 — stop/trailing/time/manual 사유로 보유 포지션을 시뮬 매도한다.

청산 평가는 순수 함수, 적용은 SimulatedPortfolio.apply_sell_fill에 위임(실현 PnL·평단 보존). 다일
dry-run 포트폴리오용. 실브로커/Robinhood/MCP/라이브 주문 없음 — real_orders_placed는 항상 0.

fail-closed: 가격 결측/무효면 청산하지 않고 data_missing 표시(미상가 매도 금지).

CRITICAL: LLM/이벤트 캘린더 실연동 없음. 전략 시그널/청산 규칙 튜닝 없음 — 시뮬 청산 메커니즘만.

spec: specs/sim_exit.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.sim_portfolio import SimulatedPortfolio, TradeRecord


class ExitReason(str, Enum):
    """시뮬 청산 사유."""

    STOP_LOSS_HIT = "stop_loss_hit"
    TRAILING_STOP_HIT = "trailing_stop_hit"
    TIME_STOP = "time_stop"
    MANUAL_SIM_EXIT = "manual_sim_exit"


@dataclass(frozen=True)
class ExitParams:
    """청산 평가 입력. None인 항목은 해당 청산 미적용."""

    stop_price: float | None = None
    trailing_high: float | None = None
    trail_pct: float | None = None
    hold_days: int | None = None
    max_hold_days: int | None = None
    manual_exit: bool = False
    exit_shares: int | None = None  # None=전량, 아니면 부분


@dataclass(frozen=True)
class ExitPolicy:
    """다일 시뮬용 상위 청산 설정(포지션 무관 비율/일수). 포지션별 ExitParams로 변환해 쓴다.

    stop_loss_pct: 진입가 대비 손절 비율(예: 0.10 = 진입가 -10%). trail_pct: 추적 고점 대비 비율.
    max_hold_days: 보유 일수 도달 시 시간청산. manual_exit_date: 그 날짜(YYYY-MM-DD)에 전량 청산.
    아무 것도 없으면 비활성(기존 동작 보존). fail-closed: 음수/0 비율, 0 이하 max_hold_days → ValueError.
    """

    stop_loss_pct: float | None = None
    trail_pct: float | None = None
    max_hold_days: int | None = None
    manual_exit_date: str | None = None

    def __post_init__(self) -> None:
        if self.stop_loss_pct is not None and not (0.0 < self.stop_loss_pct < 1.0):
            raise ValueError(f"stop_loss_pct는 (0,1) 범위여야 한다: {self.stop_loss_pct!r}")
        if self.trail_pct is not None and not (0.0 < self.trail_pct < 1.0):
            raise ValueError(f"trail_pct는 (0,1) 범위여야 한다: {self.trail_pct!r}")
        if self.max_hold_days is not None and self.max_hold_days <= 0:
            raise ValueError(f"max_hold_days는 양수여야 한다: {self.max_hold_days!r}")

    @property
    def is_active(self) -> bool:
        """청산 설정이 하나라도 있으면 True(없으면 기존 동작 그대로)."""
        return any((
            self.stop_loss_pct is not None,
            self.trail_pct is not None,
            self.max_hold_days is not None,
            self.manual_exit_date is not None,
        ))


def exit_params_for_position(
    policy: ExitPolicy, *, avg_entry_price: float, hold_days: int, manual: bool = False
) -> ExitParams:
    """상위 ExitPolicy + 포지션 상태(진입가/보유일)를 포지션별 ExitParams로 변환한다(순수).

    stop_price = 진입가 × (1 - stop_loss_pct). trail_pct는 그대로(trailing_high는 apply_exit가 포지션
    추적값을 쓴다). max_hold_days/hold_days로 시간청산. manual은 호출부가 날짜 비교로 결정.
    """
    stop_price = (
        avg_entry_price * (1.0 - policy.stop_loss_pct)
        if policy.stop_loss_pct is not None
        else None
    )
    return ExitParams(
        stop_price=stop_price,
        trail_pct=policy.trail_pct,
        hold_days=hold_days if policy.max_hold_days is not None else None,
        max_hold_days=policy.max_hold_days,
        manual_exit=manual,
    )


@dataclass(frozen=True)
class ExitDecision:
    """청산 평가 결과."""

    should_exit: bool
    reason: ExitReason | None
    shares: int
    data_missing: bool = False


@dataclass(frozen=True)
class ExitResult:
    """청산 적용 결과."""

    exited: bool
    reason: ExitReason | None
    shares: int
    realized_pnl: float
    data_missing: bool
    trade: "TradeRecord | None"
    note: str


def _bad_price(price: float | None) -> bool:
    return price is None or (isinstance(price, float) and math.isnan(price)) or price <= 0


def evaluate_exit(*, price: float | None, shares_held: int, params: ExitParams) -> ExitDecision:
    """청산 여부/사유/수량을 평가한다(순수). 우선순위: manual > stop > trailing > time.

    가격 결측/무효 → data_missing(청산 없음, fail-closed). 매도수량 = exit_shares(있으면) 아니면 전량.
    """
    if shares_held <= 0:
        return ExitDecision(False, None, 0)
    if _bad_price(price):
        return ExitDecision(False, None, 0, data_missing=True)

    shares = shares_held
    if params.exit_shares is not None:
        shares = min(max(params.exit_shares, 0), shares_held)

    reason: ExitReason | None = None
    if params.manual_exit:
        reason = ExitReason.MANUAL_SIM_EXIT
    elif params.stop_price is not None and price <= params.stop_price:
        reason = ExitReason.STOP_LOSS_HIT
    elif (
        params.trailing_high is not None
        and params.trail_pct is not None
        and price <= params.trailing_high * (1.0 - params.trail_pct)
    ):
        reason = ExitReason.TRAILING_STOP_HIT
    elif (
        params.max_hold_days is not None
        and params.hold_days is not None
        and params.hold_days >= params.max_hold_days
    ):
        reason = ExitReason.TIME_STOP

    if reason is None or shares <= 0:
        return ExitDecision(False, None, 0)
    return ExitDecision(True, reason, shares)


def apply_exit(
    portfolio: "SimulatedPortfolio", symbol: str, *, price: float | None, params: ExitParams
) -> ExitResult:
    """포지션을 평가해 충족 시 시뮬 매도한다(부분/전량). 실주문 없음."""
    pos = portfolio.positions.get(symbol)
    if pos is None:
        return ExitResult(False, None, 0, 0.0, False, None, f"{symbol}: 포지션 없음")

    # 트레일링 스탑은 포지션이 추적하는 trailing_high를 쓴다(명시 trailing_high 없을 때).
    effective = params
    if params.trail_pct is not None and params.trailing_high is None and pos.trailing_high is not None:
        effective = replace(params, trailing_high=pos.trailing_high)

    decision = evaluate_exit(price=price, shares_held=pos.shares, params=effective)
    if decision.data_missing:
        return ExitResult(False, None, 0, 0.0, True, None, f"{symbol}: 가격 결측 → fail-closed(미청산)")
    if not decision.should_exit:
        return ExitResult(False, None, 0, 0.0, False, None, f"{symbol}: 청산 조건 미충족")

    res = portfolio.apply_sell_fill(
        symbol, decision.shares, price, exit_reason=decision.reason.value
    )
    realized = res.trade.realized_pnl if res.trade is not None else 0.0
    return ExitResult(
        exited=res.applied,
        reason=decision.reason if res.applied else None,
        shares=decision.shares if res.applied else 0,
        realized_pnl=realized,
        data_missing=False,
        trade=res.trade,
        note=res.reason,
    )
