# Step 1: claude-hooks

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md` (특히 ADR-003: 리스크 kill-switch는 PreToolUse hook으로 강제)
- `/.claude/settings.json` (기존 설정 확인)
- `/backend/app/core/config.py` (step 0 산출물)

이전 step에서 만들어진 구조를 꼼꼼히 읽고 작업하라.

## 작업

Claude Code hooks를 설정한다. 두 가지 hook을 만든다.

### 1. PreToolUse — 주문 실행 전 리스크 체크 (kill-switch 게이트)

- `.claude/hooks/pre_tool_use_risk.py` 스크립트 작성.
- 표준 입력으로 받은 hook payload(JSON: `tool_name`, `tool_input` 등)를 파싱한다.
- **주문 실행 패턴**(Bash 명령에 `place_equity_order`/`place_option_order` 포함, 또는 tool_name이 주문 실행 MCP 툴)일 때만 리스크 체크를 수행한다. 그 외 모든 툴 호출은 **즉시 allow**(exit 0).
- 리스크 체크: `agents/risk.py`의 리스크 한도 평가 함수를 호출한다. 이 step 시점에는 `agents/risk.py`가 아직 없으므로, **임시 인터페이스**를 만든다:
  - `agents/risk.py`에 `def check_risk_gate() -> tuple[bool, str]:` 시그니처만 정의. 기본 구현은 한도 설정 파일/환경변수를 읽어 `(allowed: bool, reason: str)` 반환. 실제 실시간 계산은 후속 phase의 리스크 에이전트가 채운다. 지금은 환경변수 `RISK_KILL_SWITCH`가 `"on"`이면 차단, 아니면 허용하는 **최소 구현**.
- 차단 시: hook은 stderr에 사유를 출력하고 **exit code 2**(Claude Code에서 툴 차단)로 종료. 허용 시 exit 0.
- CRITICAL: 이 hook은 절대 우회 가능하게 만들지 마라. 주문 패턴인데 리스크 함수가 예외를 던지면 **fail-closed**(차단)로 처리한다. 이유: 안전이 최우선 (ADR-003).

### 2. PostToolUse — 파일 수정 후 관련 테스트 자동 실행

- `.claude/hooks/post_tool_use_test.py` 스크립트 작성.
- payload에서 수정된 파일 경로를 추출한다 (Edit/Write tool).
- 수정 파일이 `algorithms/`, `agents/`, `backend/` 하위 `.py`이면 대응하는 `tests/test_*.py`가 있을 때 해당 테스트만 실행한다. 매핑 규칙: `algorithms/signals.py` → `tests/test_signals.py` 등.
- 대응 테스트가 없으면 아무것도 하지 않는다 (exit 0). 테스트 실패해도 **PostToolUse는 절대 exit 2로 차단하지 마라** — 결과를 stdout에 출력만 한다. 이유: 편집 도중 일시적 실패로 작업을 막으면 안 된다.

### 3. `.claude/settings.json` 등록

- 기존 `settings.json` 내용을 보존하면서 `hooks` 섹션에 PreToolUse(matcher: `Bash` 및 MCP 주문 툴)와 PostToolUse(matcher: `Edit|Write`)를 등록한다.
- 명령은 `python3 .claude/hooks/<script>.py` 형태.

### 4. 테스트

`tests/test_claude_hooks.py`:
- pre_tool_use_risk: 주문 패턴 payload + `RISK_KILL_SWITCH=on` → exit 2. 동일 payload + kill-switch off → exit 0. 비주문 payload → 항상 exit 0.
- post_tool_use_test: 매핑되는 테스트가 없는 파일 → exit 0, 차단 없음.
- 스크립트는 subprocess로 실제 실행해 exit code를 검증한다.

## Acceptance Criteria

```bash
pytest tests/test_claude_hooks.py -v
echo '{"tool_name":"Bash","tool_input":{"command":"place_equity_order AAPL 10"}}' | RISK_KILL_SWITCH=on python3 .claude/hooks/pre_tool_use_risk.py; test $? -eq 2 && echo "BLOCKED OK"
echo '{"tool_name":"Read","tool_input":{}}' | python3 .claude/hooks/pre_tool_use_risk.py; test $? -eq 0 && echo "ALLOW OK"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - ADR-003(PreToolUse 리스크 게이트)을 정확히 구현했는가?
   - CLAUDE.md CRITICAL(리스크 게이트 우회 금지, fail-closed)을 지켰는가?
3. `phases/0-foundation/index.json`의 step 1을 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`
   - 개입 필요 → `"blocked"` + `"blocked_reason"`

## 금지사항

- PostToolUse hook이 테스트 실패 시 exit 2로 작업을 차단하게 만들지 마라. 이유: 편집 중 일시적 실패가 전체 작업을 막는다.
- PreToolUse 리스크 hook을 주문이 아닌 모든 툴까지 차단하게 만들지 마라. 이유: 모든 작업이 멈춘다. 주문 패턴에만 작동해야 한다.
- 리스크 함수 예외를 무시하고 allow 처리하지 마라. 이유: fail-open은 한도 초과 주문을 통과시킨다. 반드시 fail-closed.
- 기존 `.claude/settings.json` 내용을 덮어쓰지 마라. 병합하라.
- 기존 테스트를 깨뜨리지 마라.
