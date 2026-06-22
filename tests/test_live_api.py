"""`/api/live` 라우터 테스트 (spec: specs/live_session.md).

GET status / POST start·stop·emergency-halt / GET daily-record·weekly-record.
CRITICAL: 어떤 엔드포인트도 실주문을 내지 않는다(real_orders_placed=0). MCP 없으면 200 +
NOT_READY_NO_MCP(크래시 없음). stop/halt는 즉시 신규 주문 차단. Shadow는 독립적으로 동작.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import app
from backend.app.services import live_session
from backend.app.services.live_session import LiveSessionManager
from backend.app.services.robinhood_mcp import PlaceholderRobinhoodMcpAdapter
from tests.test_live_session import FakeAvailableAdapter

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_manager(tmp_path):
    """각 테스트마다 전역 매니저를 임시 reports_dir로 초기화(파일 오염 방지)."""
    live_session.set_session_manager(
        LiveSessionManager(
            adapter=PlaceholderRobinhoodMcpAdapter(),
            settings=Settings(live_trading_enabled=False),
            reports_dir=tmp_path,
        )
    )
    yield
    live_session.set_session_manager(None)


def _use_available_adapter(tmp_path, *, live_enabled=True):
    adapter = FakeAvailableAdapter()
    live_session.set_session_manager(
        LiveSessionManager(
            adapter=adapter,
            settings=Settings(live_trading_enabled=live_enabled),
            reports_dir=tmp_path,
        )
    )
    return adapter


def test_status_read_only(tmp_path):
    res = client.get("/api/live/status")
    assert res.status_code == 200
    body = res.json()
    assert body["automation_running"] is False
    assert body["real_orders_placed"] == 0


def test_start_returns_not_ready_when_mcp_missing():
    res = client.post("/api/live/start", json={"mode": "report_only"})
    assert res.status_code == 200  # 크래시 없음
    body = res.json()
    assert body["status"] == "NOT_READY_NO_MCP"
    assert body["state"]["automation_running"] is False
    assert body["real_orders_placed"] == 0


def test_start_blocked_when_live_disabled(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=False)
    res = client.post("/api/live/start", json={"mode": "live_auto"})
    assert res.json()["status"] == "BLOCKED_LIVE_DISABLED"


def test_start_blocked_when_emergency_halt(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=True)
    client.post("/api/live/emergency-halt")
    res = client.post("/api/live/start", json={"mode": "live_auto"})
    assert res.json()["status"] == "BLOCKED_EMERGENCY_HALT"


def test_successful_mocked_start_sets_running(tmp_path):
    adapter = _use_available_adapter(tmp_path, live_enabled=True)
    res = client.post("/api/live/start", json={"mode": "live_auto"})
    body = res.json()
    assert body["status"] == "OK"
    assert body["state"]["automation_running"] is True
    assert body["state"]["session_id"]
    assert adapter.order_calls == 0  # 시작은 주문 안 함


def test_stop_sets_not_running(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=True)
    client.post("/api/live/start", json={"mode": "live_auto"})
    res = client.post("/api/live/stop", json={"reason": "manual"})
    body = res.json()
    assert body["state"]["automation_running"] is False
    assert body["state"]["stop_reason"] == "manual"


def test_emergency_halt_blocks_orders(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=True)
    client.post("/api/live/start", json={"mode": "live_auto"})
    res = client.post("/api/live/emergency-halt")
    body = res.json()
    assert body["state"]["emergency_halt"] is True
    assert body["state"]["automation_running"] is False


def test_read_endpoints_place_no_orders(tmp_path):
    adapter = _use_available_adapter(tmp_path, live_enabled=True)
    # 읽기 전용 엔드포인트 반복 호출(UI 새로고침 시뮬레이션) — 주문 0.
    for _ in range(3):
        client.get("/api/live/status")
        client.get("/api/live/daily-record")
        client.get("/api/live/weekly-record")
    assert adapter.order_calls == 0


def test_daily_record_after_stop(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=True)
    client.post("/api/live/start", json={"mode": "live_auto"})
    client.post("/api/live/stop", json={"reason": "eod"})
    res = client.get("/api/live/daily-record")
    assert res.status_code == 200
    body = res.json()
    assert body is not None
    assert body["real_orders_placed"] == 0


def test_weekly_record_aggregates(tmp_path):
    _use_available_adapter(tmp_path, live_enabled=True)
    client.post("/api/live/start", json={"mode": "live_auto"})
    client.post("/api/live/stop", json={"reason": "eod"})
    res = client.get("/api/live/weekly-record")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_shadow_still_works_independently():
    # 라이브 추가가 Shadow를 깨지 않는다(분리).
    res = client.get("/api/shadow")
    assert res.status_code == 200
    assert res.json()["real_orders_placed"] == 0
