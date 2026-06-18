# Step 5: e2e-integration (전체 매매 루프 엔드투엔드 검증)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/ARCHITECTURE.md` (자동매매 루프 데이터 흐름), `/docs/ADR.md` (ADR-003)
- `/agents/base.py`, `/agents/scanner.py`, `/agents/decision.py`, `/agents/executor.py`, `/agents/risk.py`, `/agents/reporter.py`, `/agents/notifier.py`
- `/algorithms/signals.py`, `/algorithms/filters.py`, `/algorithms/sizing.py`
- `/backend/app/services/active_settings.py` (step 4), `/backend/app/services/llm.py` (step 0)

## 작업

지금까지 만든 부품을 하나의 **자동매매 루프**로 묶는 통합 검증을 작성한다. 새 기능을 만들기보다, 부품들이 설계대로 함께 동작함을 E2E로 증명한다(모두 Mock/fake — 실거래·실 OpenAI 호출 없음).

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/e2e_integration.md`

전체 흐름을 정의:
```
스캐너(워치리스트, mock 가격) → signals(Layer1) + filters(Layer2)
  → 후보 → 판단(OpenAI provider, fake client / 또는 Mock) → BUY/HOLD/SELL
  → 활성 세팅(active_settings) 반영 사이징(Layer3) → 수량/스탑
  → 리스크 게이트 통과 검사 → 실행(MockOrderProvider) → 체결
  → 리포트 집계 → 알림(Mock)
```
- 통합 오케스트레이터(있으면 `agents/orchestrator.py` 또는 테스트 내 조립): 에이전트들을 AgentRegistry로 묶고 1 사이클(tick) 실행.
- 안전 시나리오: 리스크 한도 초과 → kill-switch → 이후 주문 전부 거부.

### Step B. TEST (Red) — `tests/test_e2e_integration.py`

모두 mock/fake로(네트워크·실거래 없음):
- **해피 패스**: 명백한 상승 종목 → 후보 → BUY → 사이징(활성 세팅 반영) → 게이트 통과 → 체결 Fill 기록. risk_amount ≤ max_risk_pct×equity.
- **kill-switch**: 리스크 에이전트가 한도 초과 감지 → `registry.kill_all` → 실행 에이전트가 이후 주문 거부(provider 미호출).
- **LLM fallback**: LLMClient None(키 없음) → 판단/근거가 Mock으로 동작, 루프가 끝까지 진행.
- **공격적 활성 세팅이어도** 최종 risk_amount가 하드캡(SYSTEM_MAX_RISK_PCT) 이내.

### Step C. 구현 (Green)

- 필요한 최소 글루 코드(오케스트레이터/헬퍼)만. 기존 에이전트/알고리즘 인터페이스를 재사용.
- 새 비즈니스 로직을 만들지 말고 조립에 집중.

### Step D. 리팩터

테스트 픽스처(공통 mock provider/registry 구성) 정리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_e2e_integration.py -v
.venv/bin/python -m pytest -q
cd frontend && npm test
```

(frontend 빌드 검증이 필요하면 dev 서버를 끄고 `npm run build` — dev 중 build는 `.next` 충돌. CLAUDE.md 실수 기록 참조.)

## 검증 절차

1. 위 AC 실행. 모든 시나리오 통과.
2. 아키텍처 체크리스트:
   - 전체 루프가 설계(스캐너→3레이어→판단→게이트→실행→리포트→알림)대로 동작하는가?
   - **kill-switch·하드캡 안전 불변식이 통합 상태에서도 유지되는가? (ADR-003, 최우선)**
   - LLM 키 없이도(Mock fallback) 전체가 도는가? 실거래/실 OpenAI 호출이 없는가?
3. `phases/4-integration/index.json`의 step 5를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- E2E 테스트에서 실제 OpenAI/Robinhood를 호출하지 마라. 전부 mock/fake.
- 통합을 맞추려고 리스크 게이트/하드캡을 느슨하게 하지 마라. 이유: 시스템 최대 위험 (ADR-003).
- 새 매매 로직을 여기서 발명하지 마라. 기존 부품 조립만.
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트를 깨뜨리지 마라.
