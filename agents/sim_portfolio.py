"""시뮬레이션 포트폴리오 상태 추적 — 시뮬 체결 후 현금/포지션/노출/PnL/매매로그.

시뮬 체결(SimulatedFill)만 상태를 갱신한다. 불가능한 시뮬 주문(현금 부족, 포지션/티어 한도 초과)을
원자적으로 막는다(검증 후 커밋 — 위반이면 상태 불변).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음.

spec: specs/sim_portfolio.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 런타임 import 회피(순환 방지) — symbol/side/shares/price만 사용.
    from agents.fill import SimulatedFill

_EPS = 1e-9


@dataclass(frozen=True)
class SimulatedPosition:
    """시뮬 보유 포지션."""

    symbol: str
    shares: int
    avg_entry_price: float
    tier: str | None = None

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_entry_price

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_entry_price) * self.shares


@dataclass(frozen=True)
class TradeRecord:
    """매매로그 1건."""

    symbol: str
    side: str
    shares: int
    price: float
    notional: float
    cash_after: float
    realized_pnl: float
    exit_reason: str | None = None
    note: str = "SIMULATED — no broker / no live order"


@dataclass(frozen=True)
class PortfolioGuardConfig:
    """불가능 주문 방지 가드. None이면 해당 가드 미적용."""

    max_position_pct: float | None = None
    tier_exposure_caps: dict[str, float] | None = None
    allow_add_to_position: bool = True


@dataclass(frozen=True)
class ApplyResult:
    """체결 적용 결과. applied=False면 상태 불변 + 사유."""

    applied: bool
    reason: str
    trade: TradeRecord | None


@dataclass(frozen=True)
class PortfolioSnapshot:
    """포트폴리오 상태 스냅샷(리포트 첨부용, 불변). 가격이 주어지면 mark-to-market 반영."""

    starting_cash: float
    cash: float
    total_exposure: float
    equity: float
    realized_pnl: float
    open_positions: int
    open_symbols: tuple[str, ...]
    trade_count: int
    real_orders_placed: int = 0
    market_value: float = 0.0       # 보유 포지션 시가합(가격 없으면 cost_basis 폴백)
    unrealized_pnl: float = 0.0      # Σ (price − avg) × shares (가격 있는 포지션만)
    data_missing: bool = False       # 보유 포지션 중 가격 결측 → fail-closed 표시


class SimulatedPortfolio:
    """시뮬 현금/포지션/노출/PnL/로그를 추적한다. 실주문 없음(real_orders_placed=0)."""

    def __init__(
        self, starting_cash: float, *, guards: PortfolioGuardConfig | None = None
    ) -> None:
        self.starting_cash = starting_cash
        self._cash = starting_cash
        self._positions: dict[str, SimulatedPosition] = {}
        self._realized_pnl = 0.0
        self._trade_log: list[TradeRecord] = []
        self._guards = guards or PortfolioGuardConfig()

    # --- 읽기 전용 상태 ---

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, SimulatedPosition]:
        return dict(self._positions)

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def trade_log(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trade_log)

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음."""
        return 0

    # --- 노출/가치 (라이브 가격 없으면 cost_basis 기준) ---

    def total_exposure(self, prices: dict[str, float] | None = None) -> float:
        total = 0.0
        for sym, pos in self._positions.items():
            if prices is not None and sym in prices:
                total += pos.market_value(prices[sym])
            else:
                total += pos.cost_basis
        return total

    def equity(self, prices: dict[str, float] | None = None) -> float:
        return self._cash + self.total_exposure(prices)

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            pos.unrealized_pnl(prices[sym])
            for sym, pos in self._positions.items()
            if sym in prices
        )

    def snapshot(self, prices: dict[str, float] | None = None) -> PortfolioSnapshot:
        """현재 상태 스냅샷(가격 주면 mark-to-market). real_orders_placed는 항상 0.

        보유 포지션 중 가격이 결측이면 data_missing=True(fail-closed) — 결측분은 cost_basis로 폴백하고
        미실현 PnL은 가격 있는 포지션만 집계한다(가짜 손익 금지).
        """
        open_syms = list(self._positions)
        if prices is None:
            data_missing = len(open_syms) > 0
            unrealized = 0.0
            market_value = self.total_exposure(None)  # cost_basis 폴백
        else:
            data_missing = any(s not in prices for s in open_syms)
            unrealized = self.unrealized_pnl(prices)
            market_value = self.total_exposure(prices)
        equity = self._cash + market_value
        return PortfolioSnapshot(
            starting_cash=self.starting_cash,
            cash=self._cash,
            total_exposure=market_value,
            equity=equity,
            realized_pnl=self._realized_pnl,
            open_positions=len(open_syms),
            open_symbols=tuple(sorted(open_syms)),
            trade_count=len(self._trade_log),
            market_value=market_value,
            unrealized_pnl=unrealized,
            data_missing=data_missing,
        )

    # --- 체결 적용 ---

    def apply_buy_fill(self, fill: "SimulatedFill", *, tier: str | None = None) -> ApplyResult:
        """매수 체결을 적용한다(가드 검증 후 커밋 — 위반이면 상태 불변)."""
        notional = fill.filled_notional
        shares = fill.estimated_shares
        price = fill.fill_price
        symbol = fill.symbol

        # 1. 현금 부족.
        if notional > self._cash + _EPS:
            return ApplyResult(
                False, f"현금 부족: 필요 {notional:.2f} > 잔액 {self._cash:.2f}", None
            )

        existing = self._positions.get(symbol)

        # 2. 중복 추가 금지.
        if existing is not None and not self._guards.allow_add_to_position:
            return ApplyResult(
                False, f"{symbol}: 기존 포지션 추가 금지(allow_add_to_position=False)", None
            )

        new_shares = (existing.shares if existing else 0) + shares
        new_cost = (existing.cost_basis if existing else 0.0) + notional
        new_avg = new_cost / new_shares if new_shares else 0.0

        prospective_cash = self._cash - notional
        # equity_after = 현금 + 전 포지션 cost_basis(해당 심볼은 new_cost로 대체).
        other_cost = sum(p.cost_basis for s, p in self._positions.items() if s != symbol)
        equity_after = prospective_cash + other_cost + new_cost

        # 3. 단일 포지션 한도.
        cap = self._guards.max_position_pct
        if cap is not None and equity_after > 0 and new_cost / equity_after > cap + _EPS:
            return ApplyResult(
                False,
                f"{symbol}: 단일 포지션 한도 초과 {new_cost / equity_after:.1%} > {cap:.1%}",
                None,
            )

        # 4. 티어 노출 한도.
        caps = self._guards.tier_exposure_caps
        if tier is not None and caps and tier in caps and equity_after > 0:
            tier_cost = new_cost + sum(
                p.cost_basis for s, p in self._positions.items()
                if s != symbol and p.tier == tier
            )
            tcap = caps[tier]
            if tier_cost / equity_after > tcap + _EPS:
                return ApplyResult(
                    False,
                    f"Tier {tier} 노출 한도 초과 {tier_cost / equity_after:.1%} > {tcap:.1%}",
                    None,
                )

        # 통과 → 커밋.
        self._cash = prospective_cash
        self._positions[symbol] = SimulatedPosition(symbol, new_shares, new_avg, tier)
        trade = TradeRecord(
            symbol=symbol, side="buy", shares=shares, price=price, notional=notional,
            cash_after=self._cash, realized_pnl=0.0,
        )
        self._trade_log.append(trade)
        return ApplyResult(True, "applied", trade)

    def apply_sell_fill(
        self, symbol: str, shares: int, price: float, *, exit_reason: str | None = None
    ) -> ApplyResult:
        """매도 체결을 적용한다(실현 PnL 추적). 부분 매도 시 잔여 평단은 유지된다."""
        pos = self._positions.get(symbol)
        if pos is None or shares <= 0 or shares > pos.shares:
            return ApplyResult(False, f"{symbol}: 매도 불가(보유 부족/무효 수량)", None)

        realized = (price - pos.avg_entry_price) * shares
        self._realized_pnl += realized
        self._cash += shares * price
        remaining = pos.shares - shares
        if remaining == 0:
            del self._positions[symbol]
        else:
            self._positions[symbol] = SimulatedPosition(
                symbol, remaining, pos.avg_entry_price, pos.tier
            )
        trade = TradeRecord(
            symbol=symbol, side="sell", shares=shares, price=price,
            notional=shares * price, cash_after=self._cash, realized_pnl=realized,
            exit_reason=exit_reason,
        )
        self._trade_log.append(trade)
        return ApplyResult(True, "applied", trade)
