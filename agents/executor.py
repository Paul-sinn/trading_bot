"""실행 에이전트 — 리스크 게이트 통과 후 주문 실행 + 체결/슬리피지 기록.

spec: specs/executor_agent.md

판단 에이전트의 결과(BUY/SELL)를 받아 **리스크 게이트를 통과한 뒤에만** 주문을 실행하고,
체결을 확인하며 슬리피지를 기록한다. MCP 주문은 mock으로 추상화한다.

원칙:
- CRITICAL (CLAUDE.md / ADR-003): 모든 자동 주문은 반드시 리스크 게이트를 통과한다. 어떤
  코드 경로로도 게이트를 우회해 place_order에 도달할 수 없다. 게이트 차단·registry.killed·
  게이트 예외 시 주문은 반드시 거부(None)된다(fail-closed). 시스템 최대 위험은 한도 초과 실거래다.
- ADR-001/002: 외부(Robinhood MCP) API를 직접 호출하지 않는다. 주문은 주입된 OrderProvider로만.
  이 phase는 결정론적 MockOrderProvider만 사용한다(실호출 금지).
- 안전 최우선: 불확실(게이트 예외)하면 주문하지 않는다(fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol, runtime_checkable

from agents.base import Agent, AgentRegistry

# MockOrderProvider 기본 체결가/슬리피지(결정론).
_DEFAULT_PRICE = 100.0
_DEFAULT_SLIPPAGE = 0.0


# --- 데이터 모델 ---


@dataclass(frozen=True)
class OrderRequest:
    """주문 요청. limit_price 없으면 provider 기본가로 체결."""

    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    limit_price: float | None = None


@dataclass(frozen=True)
class Fill:
    """체결 내역. slippage = filled_price - requested_price (부호 보존)."""

    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    requested_price: float
    filled_price: float
    slippage: float


# --- 슬리피지 헬퍼 (순수) ---


def compute_slippage(requested_price: float, filled_price: float) -> float:
    """슬리피지 = 체결가 − 요청가. 부호를 보존한다(방향 해석은 소비측).

    매수(buy)에서 양수면 불리(비싸게 체결), 매도(sell)에서 음수면 불리.
    """
    return filled_price - requested_price


def build_fill(req: OrderRequest, requested_price: float, filled_price: float) -> Fill:
    """요청·체결가로 Fill을 조립한다(슬리피지 계산 포함)."""
    return Fill(
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        requested_price=requested_price,
        filled_price=filled_price,
        slippage=compute_slippage(requested_price, filled_price),
    )


# --- 주문 provider (외부 의존 주입) ---


@runtime_checkable
class OrderProvider(Protocol):
    """주문 실행 인터페이스. 구현은 Mock/Robinhood(MCP)로 분기."""

    async def place_order(self, req: OrderRequest) -> Fill: ...


class MockOrderProvider:
    """결정론적 체결 provider (TDD용).

    요청가(limit_price, 없으면 default_price) + 고정 슬리피지로 체결한다. 난수·외부 호출·
    실거래 없음. slippage를 정확히 계산해 Fill을 반환한다.
    """

    def __init__(
        self, default_price: float = _DEFAULT_PRICE, slippage: float = _DEFAULT_SLIPPAGE
    ) -> None:
        self._default_price = default_price
        self._slippage = slippage

    async def place_order(self, req: OrderRequest) -> Fill:
        requested = req.limit_price if req.limit_price is not None else self._default_price
        filled = requested + self._slippage
        return build_fill(req, requested, filled)


class RobinhoodOrderProvider:
    """실제 Robinhood MCP 주문 연동 골격.

    이 step에서는 로직을 채우지 않는다(키/인증은 후속 phase). 키가 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError. CRITICAL: 잘못 채우면 실거래 — 골격까지만.

    실제 연동 시 구조(주석):
        # mcp 클라이언트로 주문 실행 → 체결 응답 파싱 → Fill 변환:
        # resp = await self._mcp.place_order(symbol=req.symbol, side=req.side,
        #                                    quantity=req.quantity, limit_price=req.limit_price)
        # return build_fill(req, requested_price=resp.requested, filled_price=resp.filled)
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def place_order(self, req: OrderRequest) -> Fill:
        if not self._api_key:
            raise ValueError(
                "Robinhood API 키가 없다. 주문 실행 불가 (후속 phase에서 연동)."
            )
        raise NotImplementedError(
            "Robinhood MCP 주문 연동은 후속 phase에서 구현한다. "
            "현재는 키가 있어도 실주문하지 않는다."
        )


# --- 실행 에이전트 (상태 루프) ---

# 리스크 게이트 함수 시그니처: () -> (allowed, reason).
RiskGate = Callable[[], "tuple[bool, str]"]


class ExecutorAgent(Agent):
    """리스크 게이트를 통과한 주문만 실행하고 체결/슬리피지를 기록한다.

    주문 실행은 주입된 OrderProvider에 위임하고, 이 클래스는 CRITICAL 게이트 순서 강제·
    거부 처리·체결 기록만 담당한다. 게이트 통과 없이는 어떤 경로로도 place_order에 닿지 않는다.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        provider: OrderProvider,
        risk_gate: RiskGate,
        *,
        name: str = "executor",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.provider = provider
        self.risk_gate = risk_gate
        self.fills: list[Fill] = []
        self.last_rejection: str | None = None

    async def execute(self, req: OrderRequest) -> Fill | None:
        """주문을 실행한다. CRITICAL 순서를 통과해야만 Fill을 반환한다.

        ① quantity<=0 → 거부 ② registry.killed → 거부 ③ 게이트 예외 → fail-closed 거부
        ④ 게이트 차단 → 거부 ⑤ 통과 시에만 provider.place_order ⑥ Fill 기록.
        거부 시 None을 반환하고 사유를 last_rejection에 기록한다.
        """
        # ① 수량 검증 — 0/음수 거부.
        if req.quantity <= 0:
            return self._reject(f"수량 {req.quantity} <= 0 — 주문 거부.")

        # ② kill-switch 상태 거부.
        if self.registry.is_killed():
            return self._reject(
                f"registry kill 상태 — 주문 거부: {self.registry.kill_reason}"
            )

        # ③ 리스크 게이트 호출. 예외는 fail-closed(거부).
        try:
            allowed, reason = self.risk_gate()
        except Exception as exc:  # noqa: BLE001 — 게이트 평가 실패 시 안전하게 거부(fail-closed).
            return self._reject(f"리스크 게이트 예외 → fail-closed 거부: {exc}")

        # ④ 게이트 차단 거부.
        if not allowed:
            return self._reject(f"리스크 게이트 차단 — 주문 거부: {reason}")

        # ⑤ 게이트 통과 — 여기서만 주문이 나간다.
        fill = await self.provider.place_order(req)

        # ⑥ 체결 기록.
        self.fills.append(fill)
        self.last_rejection = None
        return fill

    def _reject(self, reason: str) -> None:
        """주문을 거부하고 사유를 기록한다(None 반환)."""
        self.last_rejection = reason
        return None

    async def tick(self) -> None:
        """루프 1회. 현재 step은 주문 소스 연결 전이므로 no-op.

        후속 step에서 판단 결과(BUY/SELL)와 배선한다.
        """
        return None
