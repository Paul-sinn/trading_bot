"""서브에이전트 공통 베이스 · 레지스트리 · kill-switch 버스.

spec: specs/agent_base.md

모든 서브에이전트(scanner/decision/executor/risk/reporter/notifier)가 공유하는 토대.
개별 에이전트 로직은 여기 두지 않는다 — 라이프사이클 인터페이스와 kill-switch 버스만 제공한다.

원칙:
- ADR-002: base는 외부 I/O(파일/네트워크/DB/Claude/MCP)를 직접 호출하지 않는다.
  부수효과는 각 에이전트가 주입받은 provider로 수행한다. 여기는 순수 라이프사이클·레지스트리만.
- ADR-003: kill_all은 멱등이며, 안전 최우선으로 kill 상태에서는 에이전트 재가동을 거부한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class AgentStatus(Enum):
    """에이전트 라이프사이클 상태."""

    IDLE = "idle"        # 생성 직후 기본 상태
    RUNNING = "running"  # start() 후
    STOPPED = "stopped"  # stop() 또는 kill 후
    ERROR = "error"      # 내부 오류 표시(서브클래스가 설정)


class Agent(ABC):
    """모든 서브에이전트의 추상 베이스.

    라이프사이클(IDLE→RUNNING→STOPPED)과 kill 신호만 다룬다. 실제 작업(tick)과
    외부 I/O는 서브클래스가 주입받은 provider로 수행한다.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.status = AgentStatus.IDLE
        self.killed = False

    def start(self) -> None:
        """에이전트를 가동한다. kill 상태면 거부한다(ADR-003: 안전상 재가동 금지)."""
        if self.killed:
            return
        self.status = AgentStatus.RUNNING

    def stop(self) -> None:
        """에이전트를 정지한다(멱등)."""
        self.status = AgentStatus.STOPPED

    def mark_killed(self) -> None:
        """kill 신호를 표시하고 정지한다(멱등). kill-switch가 호출한다."""
        self.killed = True
        self.stop()

    @abstractmethod
    async def tick(self) -> None:
        """1회 작업 사이클. 서브클래스가 구현한다(외부 I/O는 여기서)."""
        ...


class AgentRegistry:
    """에이전트 등록/조회 + kill-switch 버스.

    명시적으로 생성/주입하는 인스턴스다(전역 싱글톤 강제 안 함). 리스크 에이전트가
    한도 초과 시 kill_all로 전 에이전트를 강제 정지하는 단일 게이트 역할을 한다.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self.killed = False
        self.kill_reason: str | None = None

    def register(self, agent: Agent) -> None:
        """에이전트를 등록한다. 중복 이름은 ValueError.

        이미 kill 상태인 레지스트리에 등록되면 그 에이전트도 즉시 kill한다(kill 일관성).
        """
        if agent.name in self._agents:
            raise ValueError(f"이미 등록된 에이전트 이름입니다: {agent.name!r}")
        self._agents[agent.name] = agent
        if self.killed:
            agent.mark_killed()

    def get(self, name: str) -> Agent:
        """등록된 에이전트를 반환한다. 없으면 KeyError."""
        return self._agents[name]

    def all(self) -> list[Agent]:
        """등록된 모든 에이전트(등록 순서 보존)."""
        return list(self._agents.values())

    def kill_all(self, reason: str) -> None:
        """등록된 모든 에이전트를 정지하고 kill 상태로 기록한다.

        ADR-003: 멱등이다. 여러 번 호출해도 안전하며, kill_reason은 최초 호출
        사유를 유지한다(중복 리스크 이벤트가 원래 차단 사유를 덮어쓰지 않게).
        """
        for agent in self._agents.values():
            agent.mark_killed()
        if not self.killed:
            self.killed = True
            self.kill_reason = reason

    def is_killed(self) -> bool:
        return self.killed

    def reset(self) -> None:
        """kill 상태를 해제한다(수동 복구용).

        레지스트리와 각 에이전트의 killed 플래그를 해제해 재가동을 허용한다.
        에이전트 status는 강제로 되돌리지 않는다 — 재가동은 운영자가 명시적으로 start() 한다.
        """
        self.killed = False
        self.kill_reason = None
        for agent in self._agents.values():
            agent.killed = False
