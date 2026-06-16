"""Step 0 agent-base 테스트 (TDD Red→Green).

spec: specs/agent_base.md
- Agent 라이프사이클(IDLE→RUNNING→STOPPED), kill 후 start 거부.
- AgentRegistry register/get/all, 중복 이름 처리.
- CRITICAL(ADR-003): kill_all은 전 에이전트 STOPPED + is_killed True, 멱등.
- CRITICAL(ADR-002): base는 외부 I/O 없는 순수 라이프사이클.
"""

import asyncio
import inspect

import pytest

from agents.base import Agent, AgentRegistry, AgentStatus


class DummyAgent(Agent):
    """tick 호출 횟수만 세는 테스트용 더미 (외부 I/O 없음)."""

    def __init__(self, name: str):
        super().__init__(name)
        self.ticks = 0

    async def tick(self) -> None:
        self.ticks += 1


# --- AgentStatus ---


def test_status_members():
    assert {s.value for s in AgentStatus} == {"idle", "running", "stopped", "error"}


# --- Agent 라이프사이클 ---


def test_agent_starts_idle():
    a = DummyAgent("scanner")
    assert a.name == "scanner"
    assert a.status is AgentStatus.IDLE
    assert a.killed is False


def test_start_sets_running():
    a = DummyAgent("scanner")
    a.start()
    assert a.status is AgentStatus.RUNNING


def test_stop_sets_stopped():
    a = DummyAgent("scanner")
    a.start()
    a.stop()
    assert a.status is AgentStatus.STOPPED


def test_stop_is_idempotent():
    a = DummyAgent("scanner")
    a.stop()
    a.stop()
    assert a.status is AgentStatus.STOPPED


def test_tick_is_abstract():
    # Agent 자체는 추상 — 직접 인스턴스화 불가.
    with pytest.raises(TypeError):
        Agent("x")  # type: ignore[abstract]


def test_tick_is_coroutine():
    assert inspect.iscoroutinefunction(DummyAgent.tick)


def test_tick_runs():
    a = DummyAgent("scanner")
    asyncio.run(a.tick())
    assert a.ticks == 1


# --- kill 후 start 거부 (ADR-003) ---


def test_mark_killed_stops_and_flags():
    a = DummyAgent("scanner")
    a.start()
    a.mark_killed()
    assert a.killed is True
    assert a.status is AgentStatus.STOPPED


def test_killed_agent_refuses_start():
    a = DummyAgent("scanner")
    a.mark_killed()
    a.start()  # 거부되어야 함
    assert a.status is AgentStatus.STOPPED
    assert a.status is not AgentStatus.RUNNING


def test_mark_killed_is_idempotent():
    a = DummyAgent("scanner")
    a.mark_killed()
    a.mark_killed()
    assert a.killed is True
    assert a.status is AgentStatus.STOPPED


# --- AgentRegistry ---


def test_register_and_get():
    r = AgentRegistry()
    a = DummyAgent("scanner")
    r.register(a)
    assert r.get("scanner") is a


def test_all_preserves_order():
    r = AgentRegistry()
    a, b, c = DummyAgent("a"), DummyAgent("b"), DummyAgent("c")
    r.register(a)
    r.register(b)
    r.register(c)
    assert r.all() == [a, b, c]


def test_duplicate_name_raises():
    r = AgentRegistry()
    r.register(DummyAgent("scanner"))
    with pytest.raises(ValueError):
        r.register(DummyAgent("scanner"))


def test_get_missing_raises():
    r = AgentRegistry()
    with pytest.raises(KeyError):
        r.get("nope")


def test_fresh_registry_not_killed():
    r = AgentRegistry()
    assert r.is_killed() is False


# --- kill-switch 버스 (ADR-003) ---


def test_kill_all_stops_everyone():
    r = AgentRegistry()
    agents = [DummyAgent(n) for n in ("scanner", "decision", "executor")]
    for a in agents:
        a.start()
        r.register(a)
    r.kill_all("드로우다운 한도 초과")
    assert r.is_killed() is True
    assert all(a.status is AgentStatus.STOPPED for a in agents)
    assert all(a.killed is True for a in agents)


def test_kill_all_is_idempotent():
    r = AgentRegistry()
    a = DummyAgent("scanner")
    r.register(a)
    r.kill_all("first")
    r.kill_all("second")
    assert r.is_killed() is True
    assert a.status is AgentStatus.STOPPED
    # 최초 사유 유지 — 중복 이벤트가 원래 차단 사유를 덮어쓰지 않는다.
    assert r.kill_reason == "first"


def test_kill_then_start_refused_via_registry():
    r = AgentRegistry()
    a = DummyAgent("scanner")
    r.register(a)
    r.kill_all("limit")
    a.start()
    assert a.status is AgentStatus.STOPPED


def test_register_into_killed_registry_kills_agent():
    r = AgentRegistry()
    r.kill_all("limit")
    late = DummyAgent("late")
    r.register(late)
    assert late.killed is True
    assert late.status is AgentStatus.STOPPED


# --- reset (수동 복구) ---


def test_reset_clears_kill_state():
    r = AgentRegistry()
    a = DummyAgent("scanner")
    r.register(a)
    r.kill_all("limit")
    r.reset()
    assert r.is_killed() is False
    assert r.kill_reason is None
    # 에이전트 재가동 허용.
    a.start()
    assert a.status is AgentStatus.RUNNING
