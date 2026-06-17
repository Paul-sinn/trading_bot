# Step 0: goal-planner-algo (목표 기반 세팅 역산 — 순수 함수)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (목표 & 리스크, 투자성향, Layer 3 사이징)
- `/docs/ADR.md` (ADR-002 순수 함수 / ADR-003 리스크 한도 — 가장 중요)
- `/algorithms/sizing.py`, `/specs/sizing.md` (`kelly_fraction`, `position_size`, `risk_appetite_weight`, `PositionPlan` — 역산 세팅이 여기로 들어간다)
- `/agents/risk.py`, `/specs/risk_agent.md` (`RiskLimits(max_risk_pct, max_drawdown_pct, max_position_pct)` — 역산 결과가 이 형태와 호환돼야 한다)

## 작업

"목표금액 + 목표기간(개월)"으로부터 그 목표 달성에 필요한 **리스크·투자성향 세팅을 역산**하는 순수 함수를 구현한다. 외부 I/O 금지(ADR-002). 이 step은 결정론적 계산만 — AI 결합은 step 1.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/goal_planner.md`

입력/출력/엣지케이스/공식을 명확히 정의:

- **필요 수익률 역산**: `required_monthly_return(current_equity: float, target_amount: float, months: int) -> float`
  - 복리 기준 월 수익률: `(target/current)**(1/months) - 1`. 공식을 spec에 적어라.
  - 엣지: current<=0(분모/로그 불가 → 예외 또는 정의된 처리), target<=current(이미 달성 → 0 또는 음수), months<=0(예외).

- **실현가능성 판정**: `feasibility(monthly_return: float) -> Feasibility`
  - `Feasibility` enum: `REALISTIC | AMBITIOUS | UNREALISTIC`.
  - 임계값을 spec에 명시(예: 월 ≤3% 현실적, ≤8% 도전적, 초과 비현실적). 숫자는 보수적으로.

- **모드별 세팅 역산**: `derive_settings(current_equity, target_amount, months, mode: PlanMode) -> GoalDerivedSettings`
  - `PlanMode` enum: `SAFE | AGGRESSIVE`.
  - `GoalDerivedSettings`(dataclass/pydantic): `appetite: float`(0~1), `risk_limits: RiskLimits`, `stop_loss_atr_multiplier: float`, `feasibility: Feasibility`, `required_monthly_return: float`.
  - 필요 수익률이 높을수록 appetite↑·risk%↑·stop 넓게. SAFE 모드는 더 보수적으로 캡, AGGRESSIVE는 더 허용.
  - **CRITICAL (ADR-003)**: 어떤 모드·어떤 목표에서도 `risk_limits.max_risk_pct`는 **시스템 하드캡 `SYSTEM_MAX_RISK_PCT`(상수, 예 0.05)를 절대 초과하지 못한다.** SAFE 모드는 더 낮은 캡(예 ≤0.02)을 적용한다. 비현실적 목표라고 해서 하드캡을 넘기지 마라.
  - `appetite`는 [0,1], 각 한도는 정의된 범위로 clamp.

### Step B. TEST (Red) — `tests/test_goal_planner.py`

순수 함수라 mock 불필요:
- `required_monthly_return`: 알려진 입력 기댓값(예: 10000→12000, 6개월 → 약 3.1%/월). current<=0/months<=0 예외. 이미 달성 시 ≤0.
- `feasibility` 임계값 경계.
- `derive_settings`:
  - **가장 중요한 테스트**: 극단적으로 비현실적인 목표(예: 1개월에 10배)에서도 `max_risk_pct <= SYSTEM_MAX_RISK_PCT`, SAFE 모드는 SAFE 캡 이하.
  - SAFE vs AGGRESSIVE: 같은 목표에서 AGGRESSIVE의 risk%/appetite가 SAFE 이상(단 둘 다 하드캡 이하).
  - appetite·한도 값이 정의된 범위 내로 clamp.
  - 보수적 목표(긴 기간·작은 증가)에서 낮은 appetite/risk.

### Step C. 구현 (Green) — `algorithms/goal_planner.py`

- 순수 함수. `import talib` 금지. `RiskLimits`는 `agents.risk`에서 재사용(중복 정의 금지).
- `SYSTEM_MAX_RISK_PCT` 상수를 모듈 상단에 명시하고 주석으로 ADR-003 근거 표시.

### Step D. 리팩터

수익률 계산·세팅 매핑·clamp를 작은 함수로 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_goal_planner.py -v
.venv/bin/python -c "from algorithms.goal_planner import derive_settings, PlanMode, SYSTEM_MAX_RISK_PCT; s=derive_settings(10000, 100000, 1, PlanMode.AGGRESSIVE); print(s.risk_limits.max_risk_pct, s.feasibility); assert s.risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT + 1e-9"
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행. 특히 마지막 assert(비현실적 목표에서도 하드캡 미초과)가 통과해야 한다.
2. 아키텍처 체크리스트:
   - 순수 함수인가? `talib` 미사용? `RiskLimits` 재사용(중복 정의 안 함)?
   - **어떤 목표에서도 max_risk_pct가 SYSTEM_MAX_RISK_PCT를 넘지 않는가? (ADR-003, 최우선)**
3. `phases/3-goal-planner/index.json`의 step 0을 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- 비현실적 목표를 맞추려고 `max_risk_pct`를 시스템 하드캡 위로 올리지 마라. 이유: 실거래 계좌 파산 위험 — 시스템 최대 위험 (ADR-003).
- 외부 I/O(파일/네트워크/Claude)를 넣지 마라. 이유: 순수 함수 원칙(ADR-002), 테스트 불가.
- `RiskLimits`를 새로 정의하지 마라. `agents.risk`의 것을 재사용. 이유: 단일 진실.
- current_equity<=0, months<=0 분모/로그 예외를 처리하지 않으면 안 된다.
- SPEC/TEST 없이 구현부터 하지 마라(ADR-006). 기존 테스트를 깨뜨리지 마라.
