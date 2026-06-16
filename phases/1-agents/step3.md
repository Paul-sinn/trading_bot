# Step 3: decision-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (판단 에이전트: Claude API 호출, 뉴스+차트 종합, 매수/홀드/매도)
- `/docs/ADR.md` (ADR-005: Claude는 최종 게이트 / 알고리즘이 1차 필터)
- `/agents/base.py`, `/agents/scanner.py`, `/specs/scanner_agent.md` (Candidate 정의)
- `/algorithms/filters.py` (SentimentProvider 주입 패턴 참고 — 같은 스타일로)

## 작업

스캐너가 만든 후보(Candidate)에 대해 뉴스+차트를 종합해 **매수/홀드/매도**를 결정하는 판단 에이전트를 구현한다. Claude 호출은 provider로 추상화하고, 이 phase에서는 **mock**을 쓴다 (키 부재).

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/decision_agent.md`

- `Decision` enum: `BUY | HOLD | SELL`.
- `DecisionInput(candidate: Candidate, context: dict)` — 차트 시그널·필터 결과·(있으면) 뉴스 요약.
- `DecisionProvider` 인터페이스: `async def decide(inp: DecisionInput) -> DecisionResult`.
  - `MockDecisionProvider`: **결정론적** 규칙 (예: signal BULLISH + filters_passed → BUY, 아니면 HOLD). 난수/외부호출 없음.
  - `ClaudeDecisionProvider`: 골격만. `claude-sonnet-4-6` 호출 구조를 주석으로 남기되, 키 없으면 명확한 예외(`NotImplementedError`/설정 안내). 실제 호출 금지.
- `DecisionResult(decision: Decision, confidence: float, rationale: str)`.
- `DecisionAgent(Agent)`:
  - 생성자에 `AgentRegistry`, `DecisionProvider` 주입.
  - `async def decide_candidates(candidates: list[Candidate]) -> list[DecisionResult]`.
  - registry killed면 결정 스킵(빈 결과).
- 엣지케이스: 빈 후보, provider 예외(해당 후보만 HOLD로 안전 처리 — 불확실 시 보수적), confidence 범위(0~1) 클램프.

### Step B. TEST (Red) — `tests/test_decision_agent.py`

- `MockDecisionProvider`: BULLISH+통과 → BUY, 아니면 HOLD (결정론적).
- `decide_candidates` 다건 처리, 빈 후보 → 빈 결과.
- provider 예외 후보 → HOLD(안전 기본값)로 처리, 다른 후보는 정상.
- registry.killed → 스킵.
- `ClaudeDecisionProvider`는 키 없이 호출 시 명확한 예외.

### Step C. 구현 (Green) — `agents/decision.py`

- provider 주입 패턴(filters의 SentimentProvider와 일관). mock은 순수/결정론적.
- 실제 Claude API 호출 코드를 실행 경로에 넣지 마라(골격/주석만).

### Step D. 리팩터

결과 변환·클램프 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_decision_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - Claude 의존이 provider 주입으로 격리됐는가? mock이 결정론적인가? (ADR-005)
   - 불확실/예외 시 보수적(HOLD)으로 처리하는가?
3. `phases/1-agents/index.json`의 step 3을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 실제 Claude API를 호출하지 마라. 이유: 키 없음 + 비결정론으로 테스트 불가. `MockDecisionProvider` 사용.
- provider 예외 시 BUY/SELL로 처리하지 마라. 이유: 불확실할 때 매매하면 위험. HOLD가 안전 기본값.
- 알고리즘 시그널 판정을 여기서 다시 구현하지 마라. Candidate에 담긴 결과를 활용하라.
- 기존 테스트를 깨뜨리지 마라.
