# Step 2: goal-plan-api (REST 생성/적용 + 영속화)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md` (REST 규약, 상태=SQLite/SQLAlchemy, backend SSOT)
- `/backend/app/main.py`, `/backend/app/api/portfolio.py` (라우터 등록·DI 패턴)
- `/backend/app/db/models.py`, `/backend/app/db/session.py` (모델/세션 패턴 — step 5 phase1)
- `/backend/app/services/goal_plan.py`, `/specs/goal_plan_service.md` (step 1 — `generate_goal_plan`, `GoalPlan`, `GoalInput`)

## 작업

목표 계획을 **생성(자동적용 안 함)** 하고, 사용자가 **검토 후 적용**하면 활성 세팅으로 저장하는 REST 엔드포인트를 만든다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/goal_plan_api.md`

- `POST /api/goal-plan` — body: `{current_equity?, target_amount, months, mode}` → `GoalPlan` JSON 반환.
  - `current_equity` 미제공 시 포트폴리오 provider에서 조회(기본 Mock).
  - **생성만 한다. 이 호출은 활성 세팅을 바꾸지 않는다**(검토 후 적용 원칙).
- `POST /api/goal-plan/apply` — body: 적용할 `GoalPlan`(또는 plan id) → 활성 세팅으로 영속화, 저장된 레코드 반환.
- DB 모델(`backend/app/db/models.py`에 추가): `GoalPlanRecord`(target_amount, months, mode, required_monthly_return, feasibility, appetite, max_risk_pct, max_drawdown_pct, max_position_pct, stop_loss_atr_multiplier, rationale, applied: bool, created_at). 적용 시 `applied=True`로 활성 1건 유지.
- 엣지케이스: 잘못된 입력(months<=0, target<=0) → 422, 적용 전 활성 세팅 없음 처리.

### Step B. TEST (Red) — `tests/test_goal_plan_api.py`

- `POST /api/goal-plan` → 200 + GoalPlan 스키마(settings·rationale·feasibility). **활성 세팅이 바뀌지 않음** 확인.
- 비현실적 목표 → feasibility UNREALISTIC, max_risk_pct ≤ 하드캡.
- `POST /api/goal-plan/apply` → 저장되고 applied=True, 조회 시 활성 세팅 반영.
- 잘못된 입력 422.
- 인메모리 SQLite로 격리(실제 DB 오염 금지).

### Step C. 구현 (Green) — `backend/app/api/goal_plan.py` + 모델 추가 + main.py 등록

- 서비스(`generate_goal_plan`)·provider는 DI(기본 Mock). 라우터를 main.py에 등록.
- 생성과 적용을 분리(생성은 부수효과 없음).

### Step D. 리팩터

DTO 변환·영속화 헬퍼 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_goal_plan_api.py -v
.venv/bin/python -c "from backend.app.main import app; print('/api/goal-plan' in [r.path for r in app.routes])"
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 아키텍처 체크리스트:
   - 생성(`/api/goal-plan`)이 활성 세팅을 바꾸지 않는가(검토 후 적용 원칙)?
   - 외부 의존이 backend service에 격리됐는가? 테스트가 인메모리 DB로 격리되는가?
   - max_risk_pct ≤ 하드캡이 응답에서도 유지되는가?
3. `phases/3-goal-planner/index.json`의 step 2를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- `POST /api/goal-plan`(생성)에서 활성 세팅을 바꾸지 마라. 이유: 사용자 검토 후 적용 원칙 위반.
- 테스트에서 실제 파일 SQLite에 쓰지 마라. 인메모리 사용(개발 DB 오염 방지).
- 세팅 수치를 API 레이어에서 재계산/변경하지 마라. 서비스 결과를 그대로 전달.
- SPEC/TEST 없이 구현부터 하지 마라. 기존 테스트를 깨뜨리지 마라.
