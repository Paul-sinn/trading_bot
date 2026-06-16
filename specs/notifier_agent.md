# SPEC: notifier_agent (알림 에이전트 — 체결/리스크/목표달성 이벤트 → 슬랙/SMS)

알림 에이전트는 체결·리스크·목표달성 **이벤트**를 받아 슬랙/SMS 등 다중 채널로 발송한다.
발송 채널은 provider로 추상화하며, 이 phase는 **mock**(실제 네트워크 발송 없음)만 사용한다.

관련 문서: PRD(알림 = 체결·리스크·목표달성 이벤트 → 슬랙/SMS),
ARCHITECTURE(알림 에이전트), ADR-002(I/O는 에이전트가 주입받은 provider로),
`specs/agent_base.md`(Agent·AgentRegistry).

CRITICAL: 실제 슬랙/SMS를 발송하지 않는다. 자격증명이 없고 테스트가 외부로 나가면 안 된다.
이 phase는 `MockNotificationProvider`만 사용한다. `Slack/SMSNotificationProvider`는 골격 +
명확한 예외까지만(실발송 금지, 토큰/번호 연동은 후속 phase).

CRITICAL: 슬랙 토큰·전화번호 등 시크릿은 코드·테스트·로그에 하드코딩하지 않는다. config/.env
에서만 읽는다(CLAUDE.md).

CRITICAL: 한 채널의 발송 실패가 다른 채널 발송을 막지 않는다(예외 격리). risk/critical 알림이
누락되면 안 되기 때문이다(안전 최우선).

## 이벤트 모델 — `NotificationEvent`

```python
@dataclass(frozen=True)
class NotificationEvent:
    type: Literal["fill", "risk", "goal"]
    title: str
    body: str
    severity: Literal["info", "warning", "critical"]
```

- `type` — 이벤트 출처: 체결(`fill`)/리스크(`risk`)/목표달성(`goal`).
- `severity` — 심각도: `info`/`warning`/`critical`.
- 부수효과 없는 값 객체(frozen). 동일 입력 → 동일 의미.

## 알림 provider (외부 의존 주입)

### `NotificationProvider`
```python
class NotificationProvider(Protocol):
    async def send(self, event: NotificationEvent) -> bool: ...
```

### `MockNotificationProvider`
- 발송 대신 내부 리스트(`sent`)에 이벤트를 기록한다(테스트 검증용). 실제 네트워크 없음.
- `send`는 항상 `True`를 반환한다(기록 성공).

### `SlackNotificationProvider` / `SMSNotificationProvider`
- 발송 구조는 **주석으로만** 남긴다(골격). 실제 발송 금지.
- 자격증명(슬랙 토큰 / 전화번호)이 없으면 명확한 예외(`ValueError`).
- 있어도 실호출하지 않고 `NotImplementedError`.

## NotifierAgent(Agent)

```python
class NotifierAgent(Agent):
    def __init__(self, registry: AgentRegistry,
                 providers: list[NotificationProvider],
                 *, min_severity: Literal["info","warning","critical"] = "info",
                 name: str = "notifier") -> None: ...
    async def notify(self, event: NotificationEvent) -> None: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속.
- 생성자에 `AgentRegistry`, **다중 채널** `list[NotificationProvider]`, (선택) severity 임계값 주입.
- `notify(event)` — 등록된 provider들에 발송한다.
  - 각 채널 발송은 예외 격리한다(한 채널 실패가 다른 채널을 막지 않는다).
  - `risk` + `critical` 이벤트는 임계값과 무관하게 **항상 발송**한다(킬스위치 알림, 안전 최우선).
  - 그 외 이벤트는 `min_severity` 임계값 이상일 때만 발송한다.
  - provider가 없으면 예외 없이 no-op.
- `tick()` — 루프 1회: 현재 step은 이벤트 소스 연결 전이므로 no-op. 후속 step에서 배선한다.

## 엣지케이스

- provider 없음 → 예외 없이 no-op.
- 한 채널이 `send`에서 예외 → 나머지 채널은 정상 발송(격리).
- 중복 이벤트 → 들어온 만큼 그대로 발송(중복 제거는 호출측 책임, 대칭).
- `risk` + `critical` → `min_severity`가 높아도 항상 발송.
- `Slack/SMSNotificationProvider` 자격증명 없이 `send` → `ValueError`.

## 비범위 (이 step에서 하지 않음)

- 실제 슬랙/SMS 발송(주입 Mock provider만 사용).
- 이벤트 소스(executor/risk) 배선·스케줄링(후속).
- 중복 제거/레이트리밋/재시도 큐.
- 프론트/REST 노출, WebSocket push.
