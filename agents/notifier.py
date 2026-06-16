"""알림 에이전트 — 체결/리스크/목표달성 이벤트 → 슬랙/SMS 다중 채널 발송.

spec: specs/notifier_agent.md

체결·리스크·목표달성 이벤트를 받아 등록된 채널(슬랙/SMS 등)로 발송한다. 발송 채널은
provider로 추상화하며, 이 phase는 mock(실제 네트워크 발송 없음)만 사용한다.

원칙:
- ADR-002: 발송 I/O는 주입된 NotificationProvider로만 수행한다. 이 클래스는 발송 루프·
  필터·예외 격리만 담당한다.
- CRITICAL (CLAUDE.md): 한 채널 실패가 다른 채널 발송을 막지 않는다(예외 격리). risk +
  critical 알림은 임계값과 무관하게 항상 발송한다 — 킬스위치 알림 누락은 안전 사고다.
- CRITICAL (CLAUDE.md): 시크릿(슬랙 토큰/전화번호)은 config/.env에서만 읽고 코드·로그에
  하드코딩하지 않는다. 이 phase의 실제 발송 provider는 골격 + 명확한 예외까지만(실발송 금지).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from agents.base import Agent, AgentRegistry

# severity 순서(임계값 비교용). 값이 클수록 심각.
_SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


# --- 이벤트 모델 ---


@dataclass(frozen=True)
class NotificationEvent:
    """알림 이벤트. 부수효과 없는 값 객체.

    type   — 출처: 체결(fill)/리스크(risk)/목표달성(goal).
    severity — 심각도: info/warning/critical.
    """

    type: Literal["fill", "risk", "goal"]
    title: str
    body: str
    severity: Literal["info", "warning", "critical"]


# --- 알림 provider (외부 의존 주입) ---


@runtime_checkable
class NotificationProvider(Protocol):
    """알림 발송 인터페이스. 구현은 Mock/Slack/SMS로 분기."""

    async def send(self, event: NotificationEvent) -> bool: ...


class MockNotificationProvider:
    """기록만 하는 발송 provider (TDD용).

    실제 발송 대신 내부 리스트(sent)에 이벤트를 쌓는다. 난수·네트워크·외부 호출 없음.
    send는 항상 True(기록 성공)를 반환한다.
    """

    def __init__(self) -> None:
        self.sent: list[NotificationEvent] = []

    async def send(self, event: NotificationEvent) -> bool:
        self.sent.append(event)
        return True


class SlackNotificationProvider:
    """실제 슬랙 발송 연동 골격.

    이 step에서는 로직을 채우지 않는다(토큰/연동은 후속 phase). 토큰이 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError. CRITICAL: 토큰은 config/.env에서만 주입한다.

    실제 연동 시 구조(주석):
        # resp = await self._client.chat_postMessage(
        #     channel=self._channel, text=f"*{event.title}*\n{event.body}")
        # return resp["ok"]
    """

    def __init__(self, token: str | None = None, channel: str | None = None) -> None:
        self._token = token
        self._channel = channel

    async def send(self, event: NotificationEvent) -> bool:
        if not self._token:
            raise ValueError(
                "슬랙 토큰이 없다. 발송 불가 (config/.env에서 주입, 후속 phase 연동)."
            )
        raise NotImplementedError(
            "슬랙 발송 연동은 후속 phase에서 구현한다. 현재는 토큰이 있어도 실발송하지 않는다."
        )


class SMSNotificationProvider:
    """실제 SMS 발송 연동 골격.

    이 step에서는 로직을 채우지 않는다(번호/연동은 후속 phase). 번호가 없으면 명확한 예외,
    있어도 실호출하지 않고 NotImplementedError. CRITICAL: 번호는 config/.env에서만 주입한다.

    실제 연동 시 구조(주석):
        # resp = await self._client.messages.create(
        #     to=self._phone_number, from_=self._from, body=f"{event.title}: {event.body}")
        # return resp.sid is not None
    """

    def __init__(self, phone_number: str | None = None) -> None:
        self._phone_number = phone_number

    async def send(self, event: NotificationEvent) -> bool:
        if not self._phone_number:
            raise ValueError(
                "SMS 수신 번호가 없다. 발송 불가 (config/.env에서 주입, 후속 phase 연동)."
            )
        raise NotImplementedError(
            "SMS 발송 연동은 후속 phase에서 구현한다. 현재는 번호가 있어도 실발송하지 않는다."
        )


# --- 알림 에이전트 (상태 루프) ---


class NotifierAgent(Agent):
    """이벤트를 등록된 다중 채널로 발송한다(예외 격리 + severity 필터).

    발송은 주입된 provider들에 위임하고, 이 클래스는 임계값 판정·발송 루프·채널별 예외
    격리만 담당한다. risk + critical은 임계값과 무관하게 항상 발송한다(안전 최우선).
    """

    def __init__(
        self,
        registry: AgentRegistry,
        providers: list[NotificationProvider],
        *,
        min_severity: Literal["info", "warning", "critical"] = "info",
        name: str = "notifier",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.providers = providers
        self.min_severity = min_severity

    async def notify(self, event: NotificationEvent) -> None:
        """이벤트를 등록된 채널들로 발송한다.

        임계값 미만이면 건너뛰되, risk + critical은 항상 발송한다(킬스위치 알림). 각 채널
        발송은 예외 격리해 한 채널 실패가 다른 채널을 막지 않게 한다. provider 없으면 no-op.
        """
        if not self._should_send(event):
            return
        for provider in self.providers:
            await self._send_isolated(provider, event)

    def _should_send(self, event: NotificationEvent) -> bool:
        """발송 여부를 판정한다. risk+critical은 임계값과 무관하게 항상 발송."""
        if event.type == "risk" and event.severity == "critical":
            return True
        return _SEVERITY_ORDER[event.severity] >= _SEVERITY_ORDER[self.min_severity]

    async def _send_isolated(
        self, provider: NotificationProvider, event: NotificationEvent
    ) -> None:
        """단일 채널 발송. 예외를 격리해 다른 채널 발송을 막지 않는다."""
        try:
            await provider.send(event)
        except Exception:  # noqa: BLE001 — 한 채널 실패가 다른 채널을 막지 않게 격리.
            return

    async def tick(self) -> None:
        """루프 1회. 현재 step은 이벤트 소스 연결 전이므로 no-op.

        후속 step에서 executor/risk 이벤트 소스와 배선한다.
        """
        return None
