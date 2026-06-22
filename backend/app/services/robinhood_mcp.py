"""Robinhood MCP 어댑터 경계 — 라이브 실행의 단일 브로커 게이트웨이.

CRITICAL (ADR-001): 외부 브로커(Robinhood MCP) I/O는 이 backend service 레이어에만 격리한다.
Robinhood는 공개 API 키가 없다 — robinhood-trading MCP 서버로 인증/조회/주문한다.

현재 이 레포에는 Robinhood MCP가 **연동되어 있지 않다**. 그래서 placeholder 어댑터만 제공한다:
`check_availability()`는 False를 반환하고, 그 외 모든 브로커 메서드는 명확한 예외를 던진다.
**브로커 호출 성공을 위조하지 않으며, 실주문을 내지 않는다(안전 최우선).** 실연동은 추후 phase.

spec: specs/live_session.md
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.core.config import Settings


class RobinhoodMcpNotConfigured(RuntimeError):
    """Robinhood MCP가 아직 연동되지 않았음을 알리는 명확한 예외.

    placeholder 어댑터의 모든 브로커 호출(조회/주문/취소)이 이 예외를 던진다 — 실호출로
    새거나 성공을 위조하지 않기 위함. 상위(LiveSessionManager)는 이를 흡수해 안전하게 처리한다.
    """


@runtime_checkable
class RobinhoodMcpAdapter(Protocol):
    """라이브 브로커 어댑터 인터페이스. 구현은 placeholder(현재) / 실 MCP(추후)로 분기."""

    def check_availability(self) -> bool: ...
    def connect(self) -> None: ...
    def get_account_status(self) -> dict: ...
    def get_buying_power(self) -> float: ...
    def get_positions(self) -> list[dict]: ...
    def get_open_orders(self) -> list[dict]: ...
    def cancel_open_orders(self) -> int: ...
    def place_limit_buy(self, symbol: str, quantity: float, limit_price: float) -> dict: ...
    def get_order_status(self, order_id: str) -> dict: ...


_NOT_CONFIGURED = "Robinhood MCP not configured"


class PlaceholderRobinhoodMcpAdapter:
    """미연동 상태의 안전 placeholder.

    `check_availability()`만 False를 반환하고, 나머지 모든 브로커 메서드는
    `RobinhoodMcpNotConfigured`를 던진다. 실주문/실조회를 절대 시도하지 않는다.
    """

    def check_availability(self) -> bool:
        return False

    def connect(self) -> None:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def get_account_status(self) -> dict:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def get_buying_power(self) -> float:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def get_positions(self) -> list[dict]:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def get_open_orders(self) -> list[dict]:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def cancel_open_orders(self) -> int:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def place_limit_buy(self, symbol: str, quantity: float, limit_price: float) -> dict:
        # 실주문 경로 없음. 연동 전에는 절대 주문하지 않는다.
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)

    def get_order_status(self, order_id: str) -> dict:
        raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)


def get_mcp_adapter(settings: Settings | None = None) -> RobinhoodMcpAdapter:
    """설정 기반 어댑터 선택.

    현재는 항상 placeholder를 반환한다(실 MCP 미연동). `robinhood_mcp_enabled`가 True여도
    실연동 어댑터가 아직 없으므로 placeholder가 `check_availability()=False`로 안전하게 막는다.
    실 MCP 어댑터는 추후 phase에서 이 팩토리에 추가한다.
    """
    settings = settings or Settings()
    return PlaceholderRobinhoodMcpAdapter()
