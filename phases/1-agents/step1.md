# Step 1: risk-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ADR.md` (ADR-003: 리스크 한도 / kill-switch가 최우선)
- `/docs/PRD.md` (리스크 에이전트: 실시간 리스크% 계산, 한도 초과 시 전 에이전트 kill-switch)
- `/agents/base.py`, `/specs/agent_base.md` (step 0 산출물 — Agent/Registry)
- `/agents/risk.py` (기존 게이트 — `check_risk_gate`는 보존)
- `/backend/app/services/portfolio.py` (`Portfolio`, `Position` 모델)
- `/.claude/hooks/pre_tool_use_risk.py` (이 게이트를 호출하는 hook)

## 작업

기존 `agents/risk.py` 골격을 **완전한 리스크 에이전트**로 확장한다. CRITICAL: 기존 `check_risk_gate()` 시그니처/동작(RISK_KILL_SWITCH)을 유지해야 한다. hook이 의존한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/risk_agent.md`

- 실시간 리스크% 계산: `current_risk_pct(portfolio: Portfolio, limits: RiskLimits) -> float`
  - 정의: 현재 미실현 손실 + 포지션 노출이 계좌 대비 차지하는 리스크 비율. (예: 드로우다운 기반 — 피크 대비 하락률, 또는 포지션 손실 합 / total_equity). spec에 공식을 명확히 적어라.
- `RiskLimits(max_risk_pct: float, max_drawdown_pct: float, max_position_pct: float)`.
- `RiskAgent(Agent)`:
  - 생성자에 `AgentRegistry`, `PortfolioProvider`, `RiskLimits` 주입.
  - `async def tick()`: 포트폴리오 조회 → `current_risk_pct` 계산 → 한도 초과 시 `registry.kill_all(reason)` 호출.
  - `def evaluate(portfolio) -> tuple[bool, str]`: 한도 내 여부 + 사유 (순수 판정 함수, 테스트 용이).
- `check_risk_gate()` 갱신: 기존 RISK_KILL_SWITCH 동작 유지 + (선택) registry kill 상태도 반영. CRITICAL: 불확실하면 **fail-closed**(차단).
- 엣지케이스: total_equity 0(분모 0), 빈 포지션, 음수 day_pnl, limits 경계값, provider 예외(→ fail-closed).

### Step B. TEST (Red) — `tests/test_risk_agent.py`

- `current_risk_pct` 알려진 입력의 기댓값, total_equity=0 안전.
- `evaluate`: 한도 내 → (True, ...), 한도 초과 → (False, ...).
- `RiskAgent.tick()`이 한도 초과 시 registry.kill_all을 호출하고 모든 에이전트가 STOPPED 되는지 (더미 에이전트 등록 후 검증).
- 기존 `check_risk_gate` 동작(RISK_KILL_SWITCH on/off) 회귀 테스트.
- provider가 예외를 던지면 차단(fail-closed)되는지.

### Step C. 구현 (Green) — `agents/risk.py`

- 기존 함수 보존 + `RiskAgent`, `RiskLimits`, 계산 함수 추가.
- 한도 초과 판정/kill은 명시적이고 멱등이어야 한다.

### Step D. 리팩터

계산부(순수 함수)와 에이전트 루프부 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_risk_agent.py -v
echo '{"tool_name":"Bash","tool_input":{"command":"place_equity_order AAPL 10"}}' | RISK_KILL_SWITCH=on .venv/bin/python .claude/hooks/pre_tool_use_risk.py; test $? -eq 2 && echo "HOOK STILL BLOCKS OK"
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 특히 hook 차단(exit 2)이 그대로 동작해야 한다.
2. 아키텍처 체크리스트:
   - kill-switch가 registry를 통해 전 에이전트를 멈추는가? (ADR-003)
   - provider 예외 시 fail-closed인가?
   - `check_risk_gate` 기존 동작이 보존됐는가?
3. `phases/1-agents/index.json`의 step 1을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- `check_risk_gate`의 기존 RISK_KILL_SWITCH 동작을 제거·변경하지 마라. 이유: hook과 기존 테스트가 의존한다.
- 리스크 판정이 불확실/예외일 때 allow로 처리하지 마라. 이유: fail-open은 한도 초과 주문을 통과시킨다 — 시스템 최대 위험 (ADR-003).
- 실제 Robinhood/외부 API를 호출하지 마라. 주입된 `PortfolioProvider`(mock)를 쓴다.
- total_equity 0 분모를 처리하지 않으면 안 된다. 이유: ZeroDivision.
- 기존 테스트를 깨뜨리지 마라.
