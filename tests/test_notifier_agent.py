"""Step 6 notifier-agent 테스트 (TDD Red→Green).

spec: specs/notifier_agent.md
- MockNotificationProvider: 발송 대신 내부 리스트에 기록(네트워크 없음), send→True.
- notify: 다중 채널 모두에 기록되는지.
- CRITICAL: 한 채널 예외가 다른 채널 발송을 막지 않는지(격리).
- CRITICAL: risk/critical 이벤트는 임계값과 무관하게 항상 발송.
- provider 없음 → 예외 없이 no-op.
- Slack/SMSNotificationProvider: 자격증명 없으면 ValueError, 있어도 NotImplementedError(실발송 금지).
"""

import asyncio

import pytest

from agents.base import AgentRegistry
from agents.notifier import (
    MockNotificationProvider,
    NotificationEvent,
    NotificationProvider,
    NotifierAgent,
    SlackNotificationProvider,
    SMSNotificationProvider,
)


# --- 이벤트 헬퍼 ---


def _fill_event(severity="info") -> NotificationEvent:
    return NotificationEvent(
        type="fill", title="체결", body="AAPL 10주 체결", severity=severity
    )


def _risk_critical() -> NotificationEvent:
    return NotificationEvent(
        type="risk", title="리스크 한도 초과", body="kill-switch 발동", severity="critical"
    )


class _BoomProvider:
    """send에서 항상 예외를 던지는 provider(격리 검증용)."""

    async def send(self, event: NotificationEvent) -> bool:
        raise RuntimeError("채널 발송 실패")


def _agent(registry=None, providers=None, **kwargs) -> NotifierAgent:
    return NotifierAgent(registry or AgentRegistry(), providers or [], **kwargs)


# --- MockNotificationProvider ---


def test_mock_provider_is_a_notification_provider():
    assert isinstance(MockNotificationProvider(), NotificationProvider)


def test_mock_provider_records_and_returns_true():
    provider = MockNotificationProvider()
    ok = asyncio.run(provider.send(_fill_event()))
    assert ok is True
    assert len(provider.sent) == 1
    assert provider.sent[0].type == "fill"


# --- notify: 다중 채널 모두 기록 ---


def test_notify_sends_to_all_channels():
    p1, p2 = MockNotificationProvider(), MockNotificationProvider()
    agent = _agent(providers=[p1, p2])
    asyncio.run(agent.notify(_fill_event()))
    assert len(p1.sent) == 1
    assert len(p2.sent) == 1


# --- CRITICAL: 한 채널 예외가 다른 채널을 막지 않음(격리) ---


def test_one_channel_failure_does_not_block_others():
    boom, ok = _BoomProvider(), MockNotificationProvider()
    agent = _agent(providers=[boom, ok])
    # 예외 격리 — notify 자체는 예외 없이 끝나야 한다.
    asyncio.run(agent.notify(_risk_critical()))
    assert len(ok.sent) == 1


def test_failure_in_second_channel_still_delivers_first():
    ok, boom = MockNotificationProvider(), _BoomProvider()
    agent = _agent(providers=[ok, boom])
    asyncio.run(agent.notify(_fill_event()))
    assert len(ok.sent) == 1


# --- CRITICAL: risk/critical은 임계값과 무관하게 항상 발송 ---


def test_risk_critical_always_sent_even_above_threshold():
    provider = MockNotificationProvider()
    agent = _agent(providers=[provider], min_severity="critical")
    asyncio.run(agent.notify(_risk_critical()))
    assert len(provider.sent) == 1


def test_low_severity_filtered_by_threshold():
    provider = MockNotificationProvider()
    agent = _agent(providers=[provider], min_severity="warning")
    asyncio.run(agent.notify(_fill_event(severity="info")))
    assert provider.sent == []


def test_default_threshold_sends_info():
    provider = MockNotificationProvider()
    agent = _agent(providers=[provider])
    asyncio.run(agent.notify(_fill_event(severity="info")))
    assert len(provider.sent) == 1


# --- provider 없음 → no-op ---


def test_no_providers_is_noop():
    agent = _agent(providers=[])
    # 예외 없이 끝나야 한다.
    asyncio.run(agent.notify(_risk_critical()))


# --- 중복 이벤트는 들어온 만큼 발송 ---


def test_duplicate_events_sent_each_time():
    provider = MockNotificationProvider()
    agent = _agent(providers=[provider])
    event = _fill_event()
    asyncio.run(agent.notify(event))
    asyncio.run(agent.notify(event))
    assert len(provider.sent) == 2


# --- tick() no-op ---


def test_tick_is_noop():
    provider = MockNotificationProvider()
    agent = _agent(providers=[provider])
    asyncio.run(agent.tick())
    assert provider.sent == []


# --- Slack/SMSNotificationProvider 골격 ---


def test_slack_provider_without_token_raises():
    provider = SlackNotificationProvider(token=None)
    with pytest.raises(ValueError):
        asyncio.run(provider.send(_fill_event()))


def test_slack_provider_with_token_not_implemented():
    provider = SlackNotificationProvider(token="xoxb-test")
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.send(_fill_event()))


def test_sms_provider_without_number_raises():
    provider = SMSNotificationProvider(phone_number=None)
    with pytest.raises(ValueError):
        asyncio.run(provider.send(_fill_event()))


def test_sms_provider_with_number_not_implemented():
    provider = SMSNotificationProvider(phone_number="+10000000000")
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.send(_fill_event()))
