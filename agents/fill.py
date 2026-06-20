"""시뮬레이션 체결 + 슬리피지 모델 (순수).

시뮬 주문을 시뮬 체결로 변환하는 순수 수학. 실브로커/Robinhood/라이브 없음 — 어떤 주문도 전송하지
않는다. SimulatedExecutor가 RiskGate PASS(주문 생성) 분기에서만 이 모델을 부른다.

슬리피지는 단순·보수적: spread 있으면 하프스프레드(안전 디폴트 하한), 없으면 안전 디폴트. 유동성
participation이 있으면 임팩트 가산. 매수는 체결가를 올린다(불리).

spec: specs/fill.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 런타임 import 회피(순환 방지) — 구조적으로 symbol/side/quantity만 사용.
    from agents.sim_execution import SimulatedOrder


@dataclass(frozen=True)
class FillContext:
    """체결 시뮬 입력(시장 컨텍스트). spread/adv 없으면 안전 디폴트 슬리피지."""

    reference_price: float
    account_cash: float
    spread_pct: float | None = None
    adv: float | None = None
    default_slippage_pct: float = 0.0010


@dataclass(frozen=True)
class SimulatedFill:
    """시뮬 체결 결과. 어떤 브로커로도 전송되지 않는다."""

    symbol: str
    side: str
    intended_notional: float
    estimated_shares: int
    reference_price: float
    slippage_pct: float
    fill_price: float
    filled_notional: float
    cash_remaining: float
    note: str = "SIMULATED FILL — no broker / no live order"


def estimate_slippage_pct(
    *,
    spread_pct: float | None = None,
    participation: float | None = None,
    default_pct: float = 0.0010,
    participation_coeff: float = 0.10,
    max_pct: float = 0.05,
) -> float:
    """단순·보수적 슬리피지(분수). spread 있으면 하프스프레드(안전 디폴트 하한) + 유동성 임팩트, 캡."""
    base = default_pct
    if spread_pct is not None and spread_pct >= 0:
        base = max(default_pct, spread_pct / 2.0)
    impact = 0.0
    if participation is not None and participation > 0:
        impact = participation_coeff * participation
    return min(base + impact, max_pct)


def simulate_fill(order: "SimulatedOrder", ctx: FillContext) -> SimulatedFill:
    """주문 + 컨텍스트로 시뮬 체결을 만든다(순수). 매수 슬리피지는 체결가를 올린다."""
    shares = order.quantity
    ref = ctx.reference_price
    intended_notional = shares * ref

    participation = None
    if ctx.adv is not None and ctx.adv > 0:
        participation = intended_notional / ctx.adv

    slippage_pct = estimate_slippage_pct(
        spread_pct=ctx.spread_pct,
        participation=participation,
        default_pct=ctx.default_slippage_pct,
    )

    sign = 1.0 if order.side == "buy" else -1.0  # 매수는 +(불리), 매도는 -.
    fill_price = ref * (1.0 + sign * slippage_pct)
    filled_notional = shares * fill_price
    cash_remaining = ctx.account_cash - filled_notional

    return SimulatedFill(
        symbol=order.symbol,
        side=order.side,
        intended_notional=intended_notional,
        estimated_shares=shares,
        reference_price=ref,
        slippage_pct=slippage_pct,
        fill_price=fill_price,
        filled_notional=filled_notional,
        cash_remaining=cash_remaining,
    )
