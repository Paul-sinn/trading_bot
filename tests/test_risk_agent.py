"""Step 1 risk-agent 테스트 (TDD Red→Green).

spec: specs/risk_agent.md
- 순수 계산: current_loss_ratio / drawdown_ratio / position_ratio(모두 분수), total_equity=0 안전.
- evaluate: 한도 내 → (True,…), 초과 → (False,…), 경계값 허용. 한도/측정치 모두 분수.
- CRITICAL(ADR-003): tick()이 한도 초과 시 registry.kill_all → 전 에이전트 STOPPED.
- CRITICAL: provider 예외 시 fail-closed(kill).
- 회귀: check_risk_gate의 RISK_KILL_SWITCH on/off 동작 보존 + registry kill 반영.
"""

import asyncio

import pytest

from agents.base import Agent, AgentRegistry, AgentStatus
from agents.risk import (
    RiskAgent,
    RiskLimits,
    check_risk_gate,
    current_loss_ratio,
    drawdown_ratio,
    position_ratio,
    unrealized_loss,
)
from backend.app.services.portfolio import Portfolio, Position


# --- 테스트 헬퍼 ---


class DummyAgent(Agent):
    """tick 카운트만 세는 더미 (외부 I/O 없음)."""

    def __init__(self, name: str):
        super().__init__(name)
        self.ticks = 0

    async def tick(self) -> None:
        self.ticks += 1


class StubProvider:
    """고정 Portfolio를 반환하는 provider."""

    def __init__(self, portfolio: Portfolio):
        self._portfolio = portfolio

    async def get_portfolio(self) -> Portfolio:
        return self._portfolio


class BoomProvider:
    """항상 예외를 던지는 provider (fail-closed 검증용)."""

    async def get_portfolio(self) -> Portfolio:
        raise RuntimeError("MCP 조회 실패")


def make_portfolio(positions, cash=1000.0, day_pnl=0.0, total_equity=None):
    pos_value = sum(p.quantity * p.current_price for p in positions)
    return Portfolio(
        total_equity=cash + pos_value if total_equity is None else total_equity,
        cash=cash,
        positions=positions,
        day_pnl=day_pnl,
    )


# 모든 한도 분수. 1.0 = 100%(사실상 무제한).
LOOSE = RiskLimits(
    max_risk_pct=1.0,
    max_drawdown_pct=1.0,
    max_position_pct=1.0,
    max_portfolio_loss_pct=1.0,
)


# --- 순수 계산 함수 ---


def test_unrealized_loss_only_counts_losers():
    # AAPL: 이익 (+50), TSLA: 손실 (-50)
    p = make_portfolio([
        Position(symbol="AAPL", quantity=10, avg_buy_price=190, current_price=195),
        Position(symbol="TSLA", quantity=5, avg_buy_price=250, current_price=240),
    ])
    assert unrealized_loss(p) == pytest.approx(50.0)


def test_current_loss_ratio_known_value():
    # 손실 50, total_equity 200 → 0.25 (분수)
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=15)],
        cash=50.0,
        total_equity=200.0,
    )
    assert current_loss_ratio(p) == pytest.approx(0.25)


def test_current_loss_ratio_zero_equity_is_safe_inf():
    p = make_portfolio([], cash=0.0, total_equity=0.0)
    # ZeroDivision 없이 inf 반환
    assert current_loss_ratio(p) == float("inf")


def test_drawdown_ratio_with_day_loss():
    # day_pnl -100, total=900, start=1000 → 0.10 (분수)
    p = make_portfolio([], cash=900.0, day_pnl=-100.0, total_equity=900.0)
    assert drawdown_ratio(p) == pytest.approx(0.10)


def test_drawdown_ratio_no_loss_is_zero():
    p = make_portfolio([], cash=1100.0, day_pnl=100.0, total_equity=1100.0)
    assert drawdown_ratio(p) == 0.0


def test_position_ratio():
    p = make_portfolio(
        [
            Position(symbol="A", quantity=1, avg_buy_price=10, current_price=20),  # 20
            Position(symbol="B", quantity=1, avg_buy_price=10, current_price=60),  # 60
        ],
        cash=20.0,
        total_equity=100.0,
    )
    assert position_ratio(p) == pytest.approx(0.60)


def test_position_ratio_empty_is_zero():
    assert position_ratio(make_portfolio([], cash=100.0)) == 0.0


# --- evaluate ---


def test_evaluate_within_limits():
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=18)],
        cash=800.0,
        total_equity=980.0,
        day_pnl=-20.0,
    )
    agent = RiskAgent(AgentRegistry(), StubProvider(p), LOOSE)
    within, reason = agent.evaluate(p)
    assert within is True
    assert isinstance(reason, str)


def test_evaluate_blocks_on_portfolio_loss():
    # 손실 50 / equity 200 = 0.25 > 포트폴리오 정지선 0.10
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=15)],
        cash=50.0,
        total_equity=200.0,
    )
    limits = RiskLimits(
        max_risk_pct=0.10,  # 매매당 — evaluate는 쓰지 않음
        max_drawdown_pct=1.0,
        max_position_pct=1.0,
        max_portfolio_loss_pct=0.10,
    )
    agent = RiskAgent(AgentRegistry(), StubProvider(p), limits)
    within, reason = agent.evaluate(p)
    assert within is False
    assert reason


def test_evaluate_blocks_on_zero_equity():
    p = make_portfolio([], cash=0.0, total_equity=0.0)
    agent = RiskAgent(AgentRegistry(), StubProvider(p), LOOSE)
    within, _ = agent.evaluate(p)
    assert within is False


def test_evaluate_boundary_value_allowed():
    # 측정치 0.25가 정확히 한도 0.25와 같음 → 허용 (> 만 위반)
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=15)],
        cash=50.0,
        total_equity=200.0,
    )
    limits = RiskLimits(
        max_risk_pct=0.25,
        max_drawdown_pct=1.0,
        max_position_pct=1.0,
        max_portfolio_loss_pct=0.25,
    )
    agent = RiskAgent(AgentRegistry(), StubProvider(p), limits)
    within, _ = agent.evaluate(p)
    assert within is True


# --- tick() → kill_all ---


def test_tick_within_limits_does_not_kill():
    registry = AgentRegistry()
    dummy = DummyAgent("scanner")
    registry.register(dummy)
    p = make_portfolio([], cash=1000.0, total_equity=1000.0)
    agent = RiskAgent(registry, StubProvider(p), LOOSE)
    asyncio.run(agent.tick())
    assert registry.is_killed() is False
    assert dummy.status != AgentStatus.STOPPED


def test_tick_breach_kills_all_agents():
    registry = AgentRegistry()
    scanner = DummyAgent("scanner")
    executor = DummyAgent("executor")
    scanner.start()
    executor.start()
    registry.register(scanner)
    registry.register(executor)
    # 손실 50 / equity 200 = 0.25 > 정지선 0.10
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=15)],
        cash=50.0,
        total_equity=200.0,
    )
    limits = RiskLimits(
        max_risk_pct=0.10,
        max_drawdown_pct=1.0,
        max_position_pct=1.0,
        max_portfolio_loss_pct=0.10,
    )
    agent = RiskAgent(registry, StubProvider(p), limits)
    asyncio.run(agent.tick())
    assert registry.is_killed() is True
    assert scanner.status == AgentStatus.STOPPED
    assert executor.status == AgentStatus.STOPPED
    assert scanner.killed and executor.killed


def test_tick_provider_exception_fails_closed():
    registry = AgentRegistry()
    dummy = DummyAgent("scanner")
    dummy.start()
    registry.register(dummy)
    agent = RiskAgent(registry, BoomProvider(), LOOSE)
    asyncio.run(agent.tick())
    # fail-closed: 예외여도 kill_all 발동
    assert registry.is_killed() is True
    assert dummy.status == AgentStatus.STOPPED
    assert agent.status == AgentStatus.ERROR


def test_tick_is_idempotent_on_repeated_breach():
    registry = AgentRegistry()
    registry.register(DummyAgent("scanner"))
    p = make_portfolio(
        [Position(symbol="X", quantity=10, avg_buy_price=20, current_price=15)],
        cash=50.0,
        total_equity=200.0,
    )
    limits = RiskLimits(
        max_risk_pct=0.10,
        max_drawdown_pct=1.0,
        max_position_pct=1.0,
        max_portfolio_loss_pct=0.10,
    )
    agent = RiskAgent(registry, StubProvider(p), limits)
    asyncio.run(agent.tick())
    first_reason = registry.kill_reason
    asyncio.run(agent.tick())
    assert registry.kill_reason == first_reason  # 최초 사유 유지(멱등)


# --- check_risk_gate 회귀 + registry 반영 ---


def test_check_risk_gate_off_allows(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "off")
    allowed, _ = check_risk_gate()
    assert allowed is True


def test_check_risk_gate_on_blocks(monkeypatch):
    monkeypatch.setenv("RISK_KILL_SWITCH", "on")
    allowed, _ = check_risk_gate()
    assert allowed is False


def test_check_risk_gate_no_env_allows(monkeypatch):
    monkeypatch.delenv("RISK_KILL_SWITCH", raising=False)
    allowed, _ = check_risk_gate()
    assert allowed is True


def test_check_risk_gate_blocks_when_registry_killed(monkeypatch):
    monkeypatch.delenv("RISK_KILL_SWITCH", raising=False)
    registry = AgentRegistry()
    registry.kill_all("드로우다운 한도 초과")
    allowed, reason = check_risk_gate(registry)
    assert allowed is False
    assert "드로우다운" in reason


def test_check_risk_gate_allows_when_registry_alive(monkeypatch):
    monkeypatch.delenv("RISK_KILL_SWITCH", raising=False)
    registry = AgentRegistry()
    allowed, _ = check_risk_gate(registry)
    assert allowed is True
