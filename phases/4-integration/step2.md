# Step 2: llm-providers-aux (센티먼트 + 리포트 코멘트 + 시황 방향성 → OpenAI)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/ADR.md` (ADR-007 OpenAI / ADR-002·005)
- `/backend/app/services/llm.py`, `/specs/openai_client.md` (step 0 — `LLMClient`, `make_llm_client`)
- `/backend/app/services/goal_plan.py`, `/agents/decision.py` (step 1 — OpenAI provider 패턴, 이걸 그대로 따르라)
- `/algorithms/filters.py`, `/specs/filters.md` (`SentimentProvider`, `ClaudeSentimentProvider` → 교체)
- `/agents/reporter.py`, `/specs/reporter_agent.md` (`CommentProvider`, `ClaudeCommentProvider` → 교체)
- `/frontend/src/lib/api.ts` (`getDirection` → `/api/direction` — 백엔드 엔드포인트가 없으면 만든다)

## 작업

나머지 LLM 기능 3개를 OpenAI로 실연동한다. step 1의 OpenAI provider 패턴을 그대로 따르라.

**SDD → TDD 순서를 강제한다.**

### 대상

1. **뉴스 센티먼트** (`algorithms/filters.py`): `ClaudeSentimentProvider` → `OpenAISentimentProvider(LLMClient)`. CRITICAL: `algorithms/`의 순수 함수(거래량/ATR/VIX)는 건드리지 마라(ADR-002). 센티먼트는 이미 주입 provider이므로 그 구현만 OpenAI로.
2. **리포트 코멘트** (`agents/reporter.py`): `ClaudeCommentProvider` → `OpenAICommentProvider(LLMClient)`. 집계 통계를 사람 말 코멘트로.
3. **시황 방향성** (신규 `backend/app/services/direction.py` + `GET /api/direction`): 매일 9시 시황 요약 + 7일 방향성(강세/중립/약세) + 근거. `MarketDirectionProvider`(Mock + OpenAI). main.py에 라우터 등록. (프론트 direction 페이지가 이 결과를 표시한다.)

### Step A. SPEC

- `specs/filters.md` 갱신: `OpenAISentimentProvider`.
- `specs/reporter_agent.md` 갱신: `OpenAICommentProvider`.
- 신규 `specs/direction_service.md`: `MarketDirection(summary, direction: Literal["bullish","neutral","bearish"], rationale, generated_at)`, provider 인터페이스(Mock + OpenAI), `GET /api/direction` 응답 스키마.

### Step B. TEST (Red)

- **네트워크 금지** — fake `LLMClient` 주입.
- `tests/test_filters.py`: `OpenAISentimentProvider`가 fake client 응답으로 bool 판정, 키 없으면 Mock fallback. 기존 순수 함수 테스트 회귀 없음.
- `tests/test_reporter_agent.py`: `OpenAICommentProvider`가 stats 기반 코멘트 생성(fake client).
- `tests/test_direction_service.py`(신규): Mock provider 결정론, OpenAI provider(fake client) 파싱, `GET /api/direction` 200 + 스키마. 불확실/예외 → 중립(neutral) 안전 기본값.

### Step C. 구현 (Green)

- 각 provider를 step 0 `LLMClient`로 구현, 기존 Mock 유지. `backend/app/services/direction.py` + `backend/app/api/direction.py` 신규, main.py 등록.
- 응답 파싱은 안전하게(불명확 → 중립/보수).

### Step D. 리팩터

프롬프트/파싱 헬퍼 공통화(step 1과 중복 최소화).

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_filters.py tests/test_reporter_agent.py tests/test_direction_service.py -v
.venv/bin/python -c "from backend.app.main import app; print('/api/direction' in [r.path for r in app.routes])"
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크리스트:
   - `algorithms/filters.py`의 순수 함수(거래량/ATR/VIX)는 그대로인가? 센티먼트만 OpenAI provider로? (ADR-002)
   - 각 provider가 키 없을 때 Mock fallback, 불확실 시 보수(중립)인가?
   - 테스트가 네트워크 없이 도는가?
3. `phases/4-integration/index.json`의 step 2를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- `algorithms/`의 결정론 순수 함수(거래량/ATR/VIX/시그널)를 LLM 호출로 바꾸지 마라. 이유: ADR-002 위반, 비결정·테스트 불가.
- 테스트에서 실제 OpenAI를 호출하지 마라. fake LLMClient 주입.
- 방향성/센티먼트가 불확실할 때 극단(강세/약세, 강한 통과)으로 처리하지 마라. 중립/보수가 안전 기본값.
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트를 깨뜨리지 마라.
