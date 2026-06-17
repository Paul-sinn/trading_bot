"""Step 2 goal_plan_api 테스트 (TDD Red→Green).

spec: specs/goal_plan_api.md
- POST /api/goal-plan → 200 + GoalPlan 스키마. 생성은 DB/활성 세팅을 바꾸지 않는다(검토 후 적용).
- 비현실적 목표 → feasibility UNREALISTIC, max_risk_pct <= 하드캡.
- POST /api/goal-plan/apply → 저장 + applied=True, 활성 1건 유지.
- 잘못된 입력(target<=0, months<=0, current_equity<=0) → 422.
- current_equity 생략 → 포트폴리오 provider(total_equity) 사용.
- 인메모리 SQLite로 격리(파일 DB 오염 금지).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from algorithms.goal_planner import SYSTEM_MAX_RISK_PCT
from backend.app.api.goal_plan import set_session_factory
from backend.app.db.models import GoalPlanRecord
from backend.app.db.session import make_session_factory
from backend.app.main import app


@pytest.fixture()
def session_factory():
    """테스트 격리용 인메모리 SQLite 세션 팩토리 주입(파일 DB 오염 방지)."""
    factory = make_session_factory("sqlite:///:memory:")
    set_session_factory(factory)
    yield factory
    set_session_factory(None)


@pytest.fixture()
def client(session_factory):
    return TestClient(app)


def _active_records(factory) -> list[GoalPlanRecord]:
    with factory() as session:
        return list(
            session.scalars(
                select(GoalPlanRecord).where(GoalPlanRecord.applied.is_(True))
            )
        )


def _all_records(factory) -> list[GoalPlanRecord]:
    with factory() as session:
        return list(session.scalars(select(GoalPlanRecord)))


# --- 생성 (POST /api/goal-plan) ---


def test_create_returns_goal_plan_schema(client):
    resp = client.post(
        "/api/goal-plan",
        json={"current_equity": 10000.0, "target_amount": 12000.0, "months": 12, "mode": "safe"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "settings",
        "rationale",
        "summary",
        "feasibility",
        "required_monthly_return",
    }
    settings = body["settings"]
    assert set(settings.keys()) == {
        "appetite",
        "risk_limits",
        "stop_loss_atr_multiplier",
        "feasibility",
        "required_monthly_return",
    }
    assert set(settings["risk_limits"].keys()) == {
        "max_risk_pct",
        "max_drawdown_pct",
        "max_position_pct",
    }


def test_create_does_not_persist(client, session_factory):
    # 생성은 부수효과 없음 — DB에 아무것도 쓰지 않는다(검토 후 적용 원칙).
    client.post(
        "/api/goal-plan",
        json={"current_equity": 10000.0, "target_amount": 12000.0, "months": 12, "mode": "safe"},
    )
    assert _all_records(session_factory) == []


def test_create_unrealistic_goal_caps_risk(client):
    # 1개월 10배 → 비현실적. 하드캡 보존.
    resp = client.post(
        "/api/goal-plan",
        json={"current_equity": 1000.0, "target_amount": 10000.0, "months": 1, "mode": "aggressive"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["feasibility"] == "unrealistic"
    assert body["settings"]["risk_limits"]["max_risk_pct"] <= SYSTEM_MAX_RISK_PCT


def test_create_uses_portfolio_equity_when_omitted(client):
    # current_equity 생략 → 포트폴리오 provider(Mock total_equity)로 계산되어 200.
    resp = client.post(
        "/api/goal-plan",
        json={"target_amount": 12000.0, "months": 12, "mode": "safe"},
    )
    assert resp.status_code == 200
    assert "settings" in resp.json()


# --- 적용 (POST /api/goal-plan/apply) ---


def test_apply_persists_and_marks_applied(client, session_factory):
    resp = client.post(
        "/api/goal-plan/apply",
        json={"current_equity": 10000.0, "target_amount": 12000.0, "months": 12, "mode": "safe"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is True
    assert body["mode"] == "safe"
    assert body["target_amount"] == 12000.0
    assert body["months"] == 12
    assert body["max_risk_pct"] <= SYSTEM_MAX_RISK_PCT

    active = _active_records(session_factory)
    assert len(active) == 1
    assert active[0].id == body["id"]


def test_apply_keeps_single_active_record(client, session_factory):
    payload = {"current_equity": 10000.0, "target_amount": 12000.0, "months": 12, "mode": "safe"}
    first = client.post("/api/goal-plan/apply", json=payload).json()
    second = client.post(
        "/api/goal-plan/apply",
        json={"current_equity": 10000.0, "target_amount": 20000.0, "months": 6, "mode": "aggressive"},
    ).json()

    active = _active_records(session_factory)
    assert len(active) == 1
    assert active[0].id == second["id"]
    assert active[0].id != first["id"]
    # 직전 레코드는 보존되되 비활성으로 내려간다.
    assert len(_all_records(session_factory)) == 2


def test_apply_preserves_service_risk_hardcap(client):
    # 비현실적 목표를 적용해도 저장된 max_risk_pct는 하드캡을 넘지 않는다(서비스 단일 진실).
    resp = client.post(
        "/api/goal-plan/apply",
        json={"current_equity": 1000.0, "target_amount": 50000.0, "months": 1, "mode": "aggressive"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["feasibility"] == "unrealistic"
    assert body["max_risk_pct"] <= SYSTEM_MAX_RISK_PCT


# --- 검증(422) ---


@pytest.mark.parametrize(
    "payload",
    [
        {"current_equity": 10000.0, "target_amount": 0.0, "months": 12, "mode": "safe"},
        {"current_equity": 10000.0, "target_amount": 12000.0, "months": 0, "mode": "safe"},
        {"current_equity": -1.0, "target_amount": 12000.0, "months": 12, "mode": "safe"},
        {"current_equity": 10000.0, "target_amount": 12000.0, "months": 12, "mode": "bogus"},
    ],
)
def test_invalid_input_returns_422(client, payload):
    assert client.post("/api/goal-plan", json=payload).status_code == 422
