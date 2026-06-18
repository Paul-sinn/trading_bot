# Step 4: apply-settings-wiring (적용된 목표 세팅 → 매매 루프 연결)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`, `/docs/ADR.md` (ADR-003 하드캡 / ADR-002 순수 함수 / ADR-001 backend SSOT)
- `/backend/app/api/goal_plan.py` (`GoalPlanRecord` 활성(applied=True) 1건 유지 — `/apply`)
- `/backend/app/db/models.py` (`GoalPlanRecord` 필드)
- `/agents/risk.py`, `/specs/risk_agent.md` (`RiskLimits`, `RiskAgent`)
- `/agents/scanner.py` (워치리스트 스캔)
- `/algorithms/sizing.py`, `/specs/sizing.md` (`position_size`, `risk_appetite_weight` — appetite·risk 한도를 받는다)

## 작업

사용자가 "적용"한 목표 세팅(활성 `GoalPlanRecord`: appetite + RiskLimits)을 **실제 매매 루프가 사용**하게 연결한다. "그걸 기반으로 매매"의 마지막 고리.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/active_settings.md`

- `backend/app/services/active_settings.py`:
  - `ActiveSettings`(pydantic/dataclass): `appetite: float`, `risk_limits: RiskLimits`, `stop_loss_atr_multiplier: float`. 활성 세팅의 런타임 표현.
  - `load_active_settings(session_factory) -> ActiveSettings`: 활성(`applied=True`) `GoalPlanRecord`를 읽어 변환. 활성 레코드가 없으면 **안전 기본값**(보수적 default: 낮은 appetite, 시스템 기본 risk 한도) 반환.
  - CRITICAL (ADR-003): 변환 시에도 `max_risk_pct`는 시스템 하드캡(`algorithms.goal_planner.SYSTEM_MAX_RISK_PCT`) 이하로 재clamp한다. DB가 어떤 값을 갖고 있든 하드캡을 신뢰의 최종선으로 둔다.
- 매매 루프 연결:
  - `RiskAgent`/`ScannerAgent`가 `ActiveSettings`(또는 `RiskLimits`+appetite)를 주입받아 사용하도록 한다. 사이징 호출 시 `position_size(...)`에 활성 appetite 가중치·max_risk_pct를 전달.
  - 기존 생성자 시그니처를 깨지 않도록 **선택적 주입**(기본값은 기존 동작 유지)으로 추가한다.
- 엣지케이스: 활성 레코드 없음 → 보수적 기본값, DB의 max_risk_pct가 하드캡 초과(이론상) → clamp.

### Step B. TEST (Red) — `tests/test_active_settings.py`

- 인메모리 SQLite에 활성 `GoalPlanRecord` 저장 → `load_active_settings`가 그 appetite/risk_limits를 반환.
- 활성 레코드 없음 → 보수적 기본값.
- DB max_risk_pct를 하드캡 초과로 조작 → `load_active_settings`가 하드캡으로 clamp(가장 중요).
- 사이징 연결: 활성 appetite가 공격적일 때 수량이 보수적일 때보다 크되, **여전히 max_risk_pct(하드캡) 한도 내**(기존 sizing 불변식 유지).

### Step C. 구현 (Green)

- `active_settings.py` + 에이전트 선택적 주입. `position_size` 호출 경로에 활성 세팅 반영.
- 기존 테스트가 깨지지 않도록 기본 인자 유지.

### Step D. 리팩터

변환·clamp·주입 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_active_settings.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행. 특히 "DB 값이 하드캡 초과여도 clamp" 테스트 통과.
2. 아키텍처 체크리스트:
   - 활성 세팅이 매매 루프(사이징/리스크)에 실제 반영되는가?
   - **어떤 경로로도 max_risk_pct가 SYSTEM_MAX_RISK_PCT를 넘지 않는가? (ADR-003, 최우선)**
   - 활성 레코드 없을 때 보수적 기본값인가? 기존 시그니처 회귀 없는가?
3. `phases/4-integration/index.json`의 step 4를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- DB에 저장된 max_risk_pct를 그대로 신뢰해 하드캡 위로 쓰지 마라. 이유: 변조/버그 시 한도 초과 실거래 (ADR-003). 로드 시 재clamp.
- 에이전트 생성자 시그니처를 필수 인자 추가로 깨지 마라. 선택적 주입으로.
- `algorithms/`를 비순수로 만들지 마라(세팅은 인자로 전달, DB 접근은 backend/agent).
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트를 깨뜨리지 마라.
