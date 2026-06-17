"""Step 4 executor-agent 테스트 (TDD Red→Green).

spec: specs/executor_agent.md
- MockOrderProvider: 결정론적 체결 + 고정 슬리피지, slippage = filled - requested.
- execute: 게이트 통과 + 정상 요청 → Fill 반환, 슬리피지 검증.
- CRITICAL: 게이트 차단/registry.killed/quantity<=0/게이트 예외 → 거부(None) + place_order 미호출.
- RobinhoodOrderProvider: 키 없으면 ValueError, 있어도 NotImplementedError(실호출 금지).
"""

import asyncio

import pytest

from agents.base import AgentRegistry
from agents.executor import (
    ExecutorAgent,
    Fill,
    MockOrderProvider,
    OrderProvider,
    OrderRequest,
    RobinhoodOrderProvider,
)
from agents.risk import check_risk_gate


# --- 게이트 헬퍼 (테스트용) ---


def _allow_gate() -> tuple[bool, str]:
    return True, "허용"


def _block_gate() -> tuple[bool, str]:
    return False, "차단"


def _boom_gate() -> tuple[bool, str]:
    raise RuntimeError("게이트 평가 실패")


class _CountingProvider:
    """place_order 호출 횟수를 세는 provider(게이트 우회 차단 검증용)."""

    def __init__(self) -> None:
        self.calls = 0

    async def place_order(self, req: OrderRequest) -> Fill:
        self.calls += 1
        return Fill(
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            requested_price=req.limit_price or 100.0,
            filled_price=req.limit_price or 100.0,
            slippage=0.0,
        )


def _buy(symbol: str = "AAPL", quantity: int = 10, limit_price: float | None = 100.0):
    return OrderRequest(symbol=symbol, side="buy", quantity=quantity, limit_price=limit_price)


def _agent(registry=None, provider=None, gate=None) -> ExecutorAgent:
    return ExecutorAgent(
        registry or AgentRegistry(),
        provider or MockOrderProvider(),
        gate or _allow_gate,
    )


# --- MockOrderProvider 결정론 + 슬리피지 ---


def test_mock_provider_is_an_order_provider():
    assert isinstance(MockOrderProvider(), OrderProvider)


def test_mock_provider_computes_slippage():
    provider = MockOrderProvider(slippage=0.05)
    fill = asyncio.run(provider.place_order(_buy(limit_price=100.0)))
    assert fill.requested_price == 100.0
    assert fill.filled_price == pytest.approx(100.05)
    assert fill.slippage == pytest.approx(0.05)


# --- execute: 정상 경로 ---


def test_execute_returns_fill_on_allow():
    provider = MockOrderProvider(slippage=0.02)
    agent = _agent(provider=provider, gate=_allow_gate)
    fill = asyncio.run(agent.execute(_buy(limit_price=50.0)))
    assert fill is not None
    assert fill.symbol == "AAPL"
    assert fill.slippage == pytest.approx(0.02)


def test_execute_records_fill():
    agent = _agent()
    asyncio.run(agent.execute(_buy()))
    assert len(agent.fills) == 1


# --- CRITICAL: 게이트 차단 시 거부 + place_order 미호출 ---


def test_blocked_gate_rejects_and_no_order():
    provider = _CountingProvider()
    agent = _agent(provider=provider, gate=_block_gate)
    result = asyncio.run(agent.execute(_buy()))
    assert result is None
    assert provider.calls == 0


def test_kill_switch_env_blocks_order(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "on")
    provider = _CountingProvider()
    registry = AgentRegistry()
    agent = ExecutorAgent(registry, provider, lambda: check_risk_gate(registry))
    result = asyncio.run(agent.execute(_buy()))
    assert result is None
    assert provider.calls == 0


# --- CRITICAL: registry.killed → 거부 + 미호출 ---


def test_registry_killed_rejects_and_no_order():
    provider = _CountingProvider()
    registry = AgentRegistry()
    registry.kill_all("리스크 한도 초과")
    agent = _agent(registry=registry, provider=provider, gate=_allow_gate)
    result = asyncio.run(agent.execute(_buy()))
    assert result is None
    assert provider.calls == 0


# --- CRITICAL: quantity 0/음수 → 거부 + 미호출 ---


def test_zero_quantity_rejected():
    provider = _CountingProvider()
    agent = _agent(provider=provider, gate=_allow_gate)
    result = asyncio.run(agent.execute(_buy(quantity=0)))
    assert result is None
    assert provider.calls == 0


def test_negative_quantity_rejected():
    provider = _CountingProvider()
    agent = _agent(provider=provider, gate=_allow_gate)
    result = asyncio.run(agent.execute(_buy(quantity=-5)))
    assert result is None
    assert provider.calls == 0


# --- CRITICAL: 게이트 예외 → fail-closed(거부) + 미호출 ---


def test_gate_exception_fail_closed():
    provider = _CountingProvider()
    agent = _agent(provider=provider, gate=_boom_gate)
    result = asyncio.run(agent.execute(_buy()))
    assert result is None
    assert provider.calls == 0


# --- tick() no-op ---


def test_tick_is_noop():
    provider = _CountingProvider()
    agent = _agent(provider=provider)
    asyncio.run(agent.tick())
    assert provider.calls == 0
    assert agent.fills == []


# --- RobinhoodOrderProvider 골격 ---


def test_robinhood_provider_skeleton_not_implemented():
    # Robinhood는 공개 API 키가 없다 — robinhood-trading MCP 기반. 골격은 실주문하지
    # 않고 NotImplementedError로 차단한다(통합 phase에서 MCP 연동).
    provider = RobinhoodOrderProvider()
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.place_order(_buy()))
