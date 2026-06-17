# SPEC: goal_plan_api (목표 플랜 REST — 생성/적용 + 영속화)

step 1의 `generate_goal_plan`(결정론 세팅 + AI 근거)을 REST로 노출한다. 목표 플랜을 **생성**(자동
적용 안 함)하고, 사용자가 **검토 후 적용**하면 활성 세팅으로 DB에 영속화한다(검토 후 적용 원칙).

관련 문서: ARCHITECTURE(REST 규약, backend SSOT, 상태=SQLite/SQLAlchemy), ADR-001(외부 의존은
backend에 격리), ADR-003(리스크 하드캡 — 최우선), ADR-004(개발 DB SQLite), ADR-006(SDD→TDD).
재사용: `backend/app/services/goal_plan.py`(`generate_goal_plan`, `GoalPlan`, `GoalInput`,
`GoalPlanProvider`, `MockGoalPlanProvider`), `backend/app/services/portfolio.py`(`get_portfolio_provider`),
`backend/app/db/session.py`(`make_session_factory`), `backend/app/db/models.py`(`Base`).

CRITICAL (ADR-003): 세팅 수치의 단일 진실은 `algorithms.goal_planner`다. API 레이어는 수치를
재계산/변경하지 않고 **서비스 결과를 그대로** 전달·저장한다. apply도 클라이언트가 보낸 세팅을 신뢰하지
않고 **입력으로부터 서비스를 다시 호출해 결정론적으로 재생성**한 뒤 저장한다(클라이언트가 하드캡을
우회한 위험 세팅을 주입하지 못하게 한다).

## 엔드포인트

### `POST /api/goal-plan` — 생성(부수효과 없음)
- 요청 body `GoalPlanRequest`:
  ```json
  { "target_amount": float>0, "months": int>0, "mode": "safe"|"aggressive",
    "current_equity": float>0 | null(생략 가능) }
  ```
- `current_equity` 미제공/`null` → 포트폴리오 provider(기본 Mock)에서 `total_equity` 조회.
- 처리: `GoalInput` 조립 → `generate_goal_plan(inp, provider)` → `GoalPlan` 반환(200).
- **활성 세팅을 바꾸지 않는다. DB에 쓰지 않는다**(검토 후 적용 원칙).

### `POST /api/goal-plan/apply` — 적용(영속화)
- 요청 body: 동일한 `GoalPlanRequest`.
- 처리: 생성과 동일하게 `generate_goal_plan`으로 플랜을 **재생성**(결정론·단일 진실) →
  `GoalPlanRecord`로 변환 → 기존 활성(`applied=True`) 레코드를 모두 `applied=False`로 내리고
  새 레코드를 `applied=True`로 저장(활성 1건 유지) → 저장된 레코드 반환(200).

### DB 모델 — `GoalPlanRecord` (`backend/app/db/models.py`에 추가)
| 컬럼 | 타입 | 비고 |
|------|------|------|
| id | int PK | autoincrement |
| target_amount | float | |
| months | int | |
| mode | str(16) | `PlanMode.value` |
| required_monthly_return | float | |
| feasibility | str(16) | `Feasibility.value` |
| appetite | float | |
| max_risk_pct | float | `settings.risk_limits.max_risk_pct` (≤ 하드캡) |
| max_drawdown_pct | float | |
| max_position_pct | float | |
| stop_loss_atr_multiplier | float | |
| rationale | str\|None | AI(또는 fallback) 근거 |
| applied | bool | 활성 여부. 적용 시 1건만 True |
| created_at | datetime | server_default now |

## 응답 스키마
- 생성: `GoalPlan`(step 1) — `settings`(중첩)·`rationale`·`summary`·`feasibility`·`required_monthly_return`.
- 적용: `GoalPlanRecordOut` — 위 `GoalPlanRecord` 컬럼 그대로(직렬화 DTO, `from_attributes`).

## 의존성 주입(DI)
- `get_goal_plan_provider()` → 기본 `MockGoalPlanProvider`(테스트/오버라이드 지점).
- `get_portfolio_provider()` → 기존 재사용(현재 equity 조회용, 기본 Mock).
- `get_session_factory()` → 기본 설정 DB 세션 팩토리(`make_session_factory()`, lru_cache).
  테스트는 `app.dependency_overrides`로 **인메모리 SQLite** 팩토리를 주입(파일 DB 오염 금지).

## 불변식
- 생성 호출은 DB·활성 세팅을 변경하지 않는다.
- 어떤 입력에서도 응답/저장된 `max_risk_pct <= SYSTEM_MAX_RISK_PCT`(서비스가 보장, API는 그대로 전달).
- 적용 후 `applied=True` 레코드는 항상 1건(직전 활성은 False로 내려간다).

## 엣지케이스
- `target_amount<=0` 또는 `months<=0` 또는 `current_equity<=0` → 422(pydantic 검증).
- `current_equity` 생략 → 포트폴리오 provider의 `total_equity` 사용.
- 적용 전 활성 세팅 없음 → 첫 apply가 첫 활성 레코드를 만든다(정상).

## 비범위 (이 step에서 하지 않음)
- 실제 Claude/Robinhood 연동(Mock provider만).
- 활성 세팅 조회 GET 엔드포인트, 프론트 페이지(step 3).
- `derive_settings`/세팅 수치 재구현(서비스 호출만 — 단일 진실).
