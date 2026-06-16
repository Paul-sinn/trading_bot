# Step 6: notifier-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (CRITICAL: 시크릿 비노출)
- `/docs/PRD.md` (알림 에이전트: 체결·리스크·목표달성 이벤트 → 슬랙/SMS)
- `/agents/base.py`, `/agents/risk.py`, `/agents/executor.py`, `/agents/reporter.py` (이벤트 소스)

## 작업

체결/리스크/목표달성 **이벤트**를 받아 슬랙/SMS로 발송하는 알림 에이전트를 구현한다. 발송 채널은 provider로 추상화하고 이 phase에서는 **mock**(실제 발송 없음).

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/notifier_agent.md`

- `NotificationEvent(type: Literal["fill","risk","goal"], title: str, body: str, severity: Literal["info","warning","critical"])`.
- `NotificationProvider` 인터페이스: `async def send(event: NotificationEvent) -> bool`.
  - `MockNotificationProvider`: 발송 대신 내부 리스트에 기록(테스트 검증용). 실제 네트워크 없음.
  - `SlackNotificationProvider` / `SMSNotificationProvider`: 골격만, 토큰/번호 없으면 명확한 예외. **실제 발송 금지.**
- `NotifierAgent(Agent)`:
  - 생성자에 `AgentRegistry`, `list[NotificationProvider]`(다중 채널), (선택) severity 임계값 주입.
  - `async def notify(event) -> None`: 등록된 provider들에 발송. 한 채널 실패가 다른 채널을 막지 않게(각 발송 예외 격리).
  - `risk` + `critical` 이벤트는 항상 발송(킬스위치 알림). 이유: 안전.
- 엣지케이스: provider 없음, 한 채널 예외(나머지는 발송), 중복 이벤트.

### Step B. TEST (Red) — `tests/test_notifier_agent.py`

- `MockNotificationProvider`로 notify 후 이벤트가 기록되는지(다중 채널 모두).
- 한 provider가 예외를 던져도 다른 provider는 발송되는지(격리).
- risk/critical 이벤트가 발송되는지.
- provider 없음 → 예외 없이 no-op.
- `Slack/SMSNotificationProvider`는 자격증명 없이 send 시 명확한 예외.

### Step C. 구현 (Green) — `agents/notifier.py`

- provider 주입 패턴. mock은 네트워크 없이 기록만.
- 시크릿(슬랙 토큰/전화번호)은 config/.env에서만 읽고 하드코딩·로그 노출 금지.

### Step D. 리팩터

발송 루프·예외 격리 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_notifier_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 전체 테스트(phase 0의 88개 + phase 1 누적)도 통과해야 한다.
2. 아키텍처 체크리스트:
   - 발송 채널이 provider로 격리됐는가? mock이 네트워크 없이 동작하는가?
   - 한 채널 실패가 다른 채널을 막지 않는가?
   - 시크릿이 코드/로그에 노출되지 않는가? (CLAUDE.md CRITICAL)
3. `phases/1-agents/index.json`의 step 6을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 실제 슬랙/SMS를 발송하지 마라. 이유: 자격증명 없음 + 테스트가 외부로 나가면 안 됨. `MockNotificationProvider` 사용.
- 슬랙 토큰/전화번호 등 시크릿을 코드·테스트·로그에 하드코딩하지 마라. 이유: CLAUDE.md CRITICAL.
- 한 채널 예외로 전체 notify가 죽게 만들지 마라. 이유: critical 알림이 누락된다.
- 기존 테스트를 깨뜨리지 마라.
