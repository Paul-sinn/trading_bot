"""리스크 에이전트 — 실시간 리스크% 계산 + kill-switch 게이트.

spec: specs/risk_agent.md

ADR-003: 모든 주문은 PreToolUse hook을 통해 `check_risk_gate`를 통과해야 한다. 리스크 에이전트는
포트폴리오를 주기적으로 평가해 한도 초과 시 `AgentRegistry.kill_all`로 전 에이전트를 강제 정지한다.

원칙:
- 안전(리스크 차단) 최우선. 판단이 불확실하거나 예외가 나면 **fail-closed**(차단/kill).
- ADR-002: 계산부는 부수효과 없는 순수 함수. I/O(포트폴리오 조회)는 tick() 루프에서 주입 provider로만.
- 외부(Robinhood/Claude) API를 직접 호출하지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.base import Agent, AgentRegistry, AgentStatus

if TYPE_CHECKING:
    # 타입 힌트 전용. 런타임 import를 피해 check_risk_gate가 pydantic 등 무거운 의존성 없이
    # import되게 한다(PreToolUse hook이 가벼운 인터프리터에서도 게이트를 부를 수 있도록).
    from backend.app.services.portfolio import Portfolio, PortfolioProvider


def check_risk_gate(registry: AgentRegistry | None = None) -> tuple[bool, str]:
    """리스크 한도를 평가해 주문 허용 여부를 반환한다.

    Args:
        registry: (선택) 에이전트 레지스트리. 주입되고 kill 상태면 차단한다.

    Returns:
        (allowed, reason): 허용 여부와 사유.

    CRITICAL: 기존 동작 보존 — 환경변수 `RISK_KILL_SWITCH`가 `"on"`이면 차단(무인자 호출 호환).
    추가로 registry가 kill 상태면 차단한다. 둘 중 하나라도 차단 신호면 차단(fail-closed).
    """
    kill_switch = os.environ.get("RISK_KILL_SWITCH", "").strip().lower()
    if kill_switch == "on":
        return False, "RISK_KILL_SWITCH가 활성화되어 주문이 차단되었습니다."
    if registry is not None and registry.is_killed():
        return False, f"리스크 kill-switch 발동: {registry.kill_reason}"
    return True, "리스크 한도 내 — 주문 허용."


@dataclass(frozen=True)
class RiskLimits:
    """리스크 한도 설정.

    단위 통일(CRITICAL): 모든 한도는 **분수**(0.05 = 5%)다. sizing.position_size와
    goal_planner.derive_settings가 분수로 소비/생성하는 것과 일치한다. 과거 risk 측정함수가
    퍼센트(×100)를 쓰면서 분수 한도와 비교해 100× 어긋나던 버그를 제거했다.

    의미 분리(CRITICAL): `max_risk_pct`(1회 매매당 리스크, sizing 전용)와
    `max_portfolio_loss_pct`(포트폴리오 전체 미실현 손실 정지선, RiskAgent 전용)는
    서로 다른 개념이므로 별도 필드다. RiskAgent.evaluate는 max_risk_pct를 쓰지 않는다.
    """

    max_risk_pct: float            # 1회 매매당 감수 리스크 (sizing 전용, 분수)
    max_drawdown_pct: float        # 당일 드로우다운 정지선 (분수)
    max_position_pct: float        # 단일 포지션 노출 한도 (분수, of total_equity)
    max_portfolio_loss_pct: float  # 포트폴리오 미실현 손실 정지선 (RiskAgent 전용, 분수)


# --- 순수 계산 함수 (부수효과 없음 — ADR-002). 모두 분수(0.05=5%)를 반환한다. ---


def unrealized_loss(portfolio: Portfolio) -> float:
    """손실 중인 포지션의 미실현 손실 합(양수, 달러). 이익 포지션은 0 기여."""
    return sum(
        max(0.0, (p.avg_buy_price - p.current_price) * p.quantity)
        for p in portfolio.positions
    )


def current_loss_ratio(portfolio: Portfolio) -> float:
    """계좌 대비 현재 미실현 손실 비율(분수) = 미실현 손실 / total_equity.

    분수(0.05 = 5%)로 반환한다 — RiskLimits 분수 단위와 일치. `total_equity <= 0`이면
    무효 계좌 상태 → `inf`(ZeroDivision 없이 안전, evaluate에서 차단).
    """
    if portfolio.total_equity <= 0:
        return float("inf")
    return unrealized_loss(portfolio) / portfolio.total_equity


def drawdown_ratio(portfolio: Portfolio) -> float:
    """당일 피크(시가) 대비 하락률(분수). day_pnl로 추정한다.

    시작 자산 = total_equity - day_pnl(당일 손익 되돌림). day_pnl >= 0이면 0.0.
    start_equity <= 0이면 inf.
    """
    if portfolio.day_pnl >= 0:
        return 0.0
    start_equity = portfolio.total_equity - portfolio.day_pnl
    if start_equity <= 0:
        return float("inf")
    return (-portfolio.day_pnl) / start_equity


def position_ratio(portfolio: Portfolio) -> float:
    """가장 큰 단일 포지션의 시장가치가 total_equity에서 차지하는 비율(분수).

    포지션 없음 → 0.0. total_equity <= 0 → inf.
    """
    if not portfolio.positions:
        return 0.0
    if portfolio.total_equity <= 0:
        return float("inf")
    largest = max(p.quantity * p.current_price for p in portfolio.positions)
    return largest / portfolio.total_equity


# --- 리스크 에이전트 (상태 루프) ---


class RiskAgent(Agent):
    """포트폴리오를 평가해 한도 초과 시 전 에이전트를 kill-switch로 정지한다.

    계산은 순수 함수에 위임하고, 이 클래스는 I/O(provider 조회)와 kill 결정만 한다.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        provider: PortfolioProvider,
        limits: RiskLimits,
        name: str = "risk",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.provider = provider
        self.limits = limits

    def evaluate(self, portfolio: Portfolio) -> tuple[bool, str]:
        """한도 내 여부 + 사유를 반환하는 순수 판정 함수. 첫 위반에서 차단한다.

        경계값(측정치 == 한도)은 허용한다(`>`만 위반).
        """
        if portfolio.total_equity <= 0:
            return False, "total_equity <= 0 — 무효 계좌 상태"

        # 포트폴리오 미실현 손실 정지선 (max_risk_pct(매매당)가 아니라 전용 필드 사용).
        loss = current_loss_ratio(portfolio)
        if loss > self.limits.max_portfolio_loss_pct:
            return False, (
                f"미실현 손실 {loss * 100:.2f}% > 한도 "
                f"{self.limits.max_portfolio_loss_pct * 100:.2f}%"
            )

        dd = drawdown_ratio(portfolio)
        if dd > self.limits.max_drawdown_pct:
            return False, (
                f"드로우다운 {dd * 100:.2f}% > 한도 "
                f"{self.limits.max_drawdown_pct * 100:.2f}%"
            )

        pos = position_ratio(portfolio)
        if pos > self.limits.max_position_pct:
            return False, (
                f"포지션 노출 {pos * 100:.2f}% > 한도 "
                f"{self.limits.max_position_pct * 100:.2f}%"
            )

        return True, "리스크 한도 내"

    async def tick(self) -> None:
        """루프 1회: 포트폴리오 조회 → 평가 → 한도 초과 시 kill_all.

        provider 예외는 fail-closed: kill_all 발동 + status ERROR. kill_all은 멱등이라
        연속 위반 tick이어도 안전하며 최초 사유를 유지한다(agent_base 보장).
        """
        try:
            portfolio = await self.provider.get_portfolio()
        except Exception as exc:  # noqa: BLE001 — 어떤 예외든 안전하게 차단(fail-closed).
            self.registry.kill_all(f"포트폴리오 조회 실패 → fail-closed: {exc}")
            self.status = AgentStatus.ERROR
            return

        within, reason = self.evaluate(portfolio)
        if not within:
            self.registry.kill_all(reason)
