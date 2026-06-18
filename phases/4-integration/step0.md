# Step 0: openai-client (공유 OpenAI 클라이언트 + 설정)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (AI 제공자 = OpenAI, 키 부재 시 Mock fallback)
- `/docs/ADR.md` (ADR-007: LLM 제공자 OpenAI / ADR-001: 외부 API는 backend 격리 / ADR-005: LLM은 근거만)
- `/.env.example` (OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL)
- `/backend/app/core/config.py` (Settings — 여기에 OpenAI 설정 추가)
- `/backend/app/services/goal_plan.py` (ClaudeGoalPlanProvider 골격 — 다음 step에서 OpenAI로 교체될 패턴 참고)

## 작업

모든 LLM 기능이 공유할 **OpenAI 클라이언트 래퍼**와 설정을 만든다. 이 step은 공통 토대만 — 개별 provider 교체는 step 1·2.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/openai_client.md`

- `config.Settings`에 추가: `openai_api_key: str | None = None`, `openai_model: str = "gpt-4o"`, `openai_base_url: str | None = None`. (기존 `claude_api_key`는 제거하거나 deprecated 주석.)
- `backend/app/services/llm.py`:
  - `requirements.txt`에 `openai` 추가.
  - `LLMClient` 인터페이스: `async def complete(self, system: str, user: str, *, max_tokens: int = ...) -> str` (텍스트 응답 반환).
  - `OpenAILLMClient(LLMClient)`: 실제 OpenAI Chat Completions 호출. 생성자에 api_key/model/base_url 주입(없으면 config에서). **실제 네트워크 호출은 여기에만**.
  - `make_llm_client(settings=None) -> LLMClient | None`: 키가 있으면 `OpenAILLMClient`, 없으면 `None`(호출부가 Mock으로 fallback하도록).
- 엣지케이스: 키 없음 → `make_llm_client`가 None, OpenAI 호출 예외 → 호출부에서 잡아 fallback(이 클래스는 예외를 그대로 전파, 격리는 provider가).

### Step B. TEST (Red) — `tests/test_openai_client.py`

- **네트워크 호출 절대 금지**. OpenAI SDK 클라이언트를 가짜(fake)로 주입하거나 monkeypatch해서 `OpenAILLMClient.complete`가 주어진 fake 응답을 텍스트로 반환하는지 검증.
- `make_llm_client(Settings(openai_api_key=None))` → None.
- `make_llm_client(Settings(openai_api_key="sk-test", openai_model="gpt-4o"))` → `OpenAILLMClient` 인스턴스(생성만, 호출 안 함).
- `config.Settings()`가 openai_model 기본값 "gpt-4o"를 갖는지.

### Step C. 구현 (Green) — `backend/app/core/config.py`, `backend/app/services/llm.py`, `requirements.txt`

- OpenAI 호출은 의존성 주입 가능하게(테스트가 fake client 주입). 실제 SDK import는 함수/메서드 내부 지연 import로 두어, openai 미설치 환경에서도 import 에러로 전체가 죽지 않게 한다.
- CRITICAL: 키/시크릿을 로그에 출력하지 마라.

### Step D. 리팩터

클라이언트 생성·호출 로직 분리.

## Acceptance Criteria

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_openai_client.py -v
.venv/bin/python -c "from backend.app.core.config import Settings; print(Settings().openai_model)"
.venv/bin/python -c "from backend.app.services.llm import make_llm_client; from backend.app.core.config import Settings; print(make_llm_client(Settings(openai_api_key=None)))"
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행. 특히 키 없을 때 `make_llm_client`가 None을 반환해야 한다.
2. 아키텍처 체크리스트:
   - OpenAI 호출이 backend service(`llm.py`)에만 격리됐는가? (ADR-001)
   - 테스트가 네트워크 없이(fake/monkeypatch) 동작하는가?
   - 키 부재 시 None(→ Mock fallback) 안전 기본값인가?
   - 시크릿이 로그에 노출되지 않는가?
3. `phases/4-integration/index.json`의 step 0을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 테스트에서 실제 OpenAI API를 호출하지 마라. 이유: 키 필요·비용·비결정·CI 실패. fake/monkeypatch 사용.
- `import openai`를 모듈 최상단에 두어 미설치 시 전체 import가 깨지게 하지 마라. 지연 import 사용.
- 시크릿(api_key)을 로그·예외 메시지·커밋에 노출하지 마라.
- 개별 provider(goal_plan/decision 등)를 이 step에서 교체하지 마라. 이유: step 1·2 범위.
- SPEC/TEST 없이 구현부터 하지 마라(ADR-006). 기존 테스트를 깨뜨리지 마라.
