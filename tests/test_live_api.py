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
from backend.app.services.market_data import MockMarketDataProvider
from backend.app.services.robinhood_mcp import PlaceholderRobinhoodMcpAdapter
from tests.test_live_session import FakeAvailableAdapter

client = TestClient(app)


def _set_manager(tmp_path, *, adapter=None, live_enabled=False, provider="mock"):
    mgr = LiveSessionManager(
        adapter=adapter if adapter is not None else PlaceholderRobinhoodMcpAdapter(),
        market_data=MockMarketDataProvider(),
        settings=Settings(live_trading_enabled=live_enabled, market_data_provider=provider),
        reports_dir=tmp_path,
    )
    live_session.set_session_manager(mgr)
    return mgr


@pytest.fixture(autouse=True)
def _reset_manager(tmp_path):
    """각 테스트마다 전역 매니저를 임시 reports_dir로 초기화(파일 오염 방지) + 스캔 스레드 정리."""
    _set_manager(tmp_path)
    yield
    mgr = live_session.get_session_manager()
    mgr.shutdown()  # 스캔 daemon 스레드 join(누수 방지)
    live_session.set_session_manager(None)


def _use_available_adapter(tmp_path, *, live_enabled=True):
    adapter = FakeAvailableAdapter()
    _set_manager(tmp_path, adapter=adapter, live_enabled=live_enabled)
    return adapter


def test_status_read_only(tmp_path):
    res = client.get("/api/live/status")
    assert res.status_code == 200
    body = res.json()
    assert body["automation_running"] is False
    assert body["real_orders_placed"] == 0


def test_report_only_start_works_without_mcp(tmp_path):
    # report_only는 MCP 없이도 모니터링 시작(시장데이터 mock). 실주문 경로 없음.
    res = client.post("/api/live/start", json={"mode": "report_only"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "OK"
    assert body["state"]["automation_running"] is True
    assert body["state"]["live_scan_running"] is True
    assert body["state"]["market_data_provider"] == "mock"
    assert body["real_orders_placed"] == 0


def test_live_auto_start_returns_not_ready_when_mcp_missing(tmp_path):
    # live_auto는 여전히 MCP 필요 — 없으면 NOT_READY_NO_MCP(크래시 없음, automation false).
    _set_manager(tmp_path, live_enabled=True)  # placeholder MCP → 미가용
    res = client.post("/api/live/start", json={"mode": "live_auto"})
    assert res.status_code == 200
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


# --- 라이브 스캔 API ---

def test_status_includes_scan_fields_after_start(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    body = client.get("/api/live/status").json()
    assert body["market_data_provider"] == "mock"
    assert body["live_scan_running"] is True
    assert body["last_scan_at"] is not None
    assert body["last_scan_event_count"] > 0


def test_scan_events_endpoint_returns_events_after_start(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    res = client.get("/api/live/scan-events?limit=50")
    assert res.status_code == 200
    events = res.json()
    assert len(events) > 0
    assert all(e["real_orders_placed"] == 0 for e in events)
    assert all(e["scan_status"] in
               {"BUY_CANDIDATE", "REJECT", "SKIP", "INSUFFICIENT_DATA", "ERROR"} for e in events)


def test_status_read_does_not_start_scan(tmp_path):
    # 시작 전 GET status/scan-events는 스캔을 시작하지 않는다(읽기 전용).
    for _ in range(3):
        body = client.get("/api/live/status").json()
        assert body["live_scan_running"] is False
        assert client.get("/api/live/scan-events").json() == []


def test_scan_stops_after_stop(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    assert client.get("/api/live/status").json()["live_scan_running"] is True
    client.post("/api/live/stop", json={"reason": "manual"})
    body = client.get("/api/live/status").json()
    assert body["live_scan_running"] is False
    assert body["automation_running"] is False


def test_scan_stops_after_emergency_halt(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    client.post("/api/live/emergency-halt")
    body = client.get("/api/live/status").json()
    assert body["live_scan_running"] is False
    assert body["automation_running"] is False


def test_scan_events_read_only_no_orders(tmp_path):
    adapter = _use_available_adapter(tmp_path, live_enabled=False)
    client.post("/api/live/start", json={"mode": "report_only"})
    for _ in range(3):
        client.get("/api/live/scan-events")
        client.get("/api/live/status")
    # report_only 스캔/조회는 브로커 주문을 절대 내지 않는다.
    assert adapter.order_calls == 0


# --- Mock LLM 파이프라인 API ---

def test_candidates_and_intents_after_start(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    cands = client.get("/api/live/candidates?limit=50").json()
    intents = client.get("/api/live/order-intents?limit=50").json()
    assert len(cands) > 0
    assert len(intents) > 0
    assert all(i["real_orders_placed"] == 0 for i in intents)
    assert all(i["status"] == "DRY_RUN_INTENT_ONLY" for i in intents)
    assert all(i["broker_order_id"] is None for i in intents)


def test_ai_status_endpoint_zero_cost(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    body = client.get("/api/ai/status").json()
    assert body["llm_provider"] == "mock"
    assert body["ai_cost_estimate_today"] == 0.0
    assert body["ai_calls_today"] > 0


def test_status_includes_pipeline_fields(tmp_path):
    client.post("/api/live/start", json={"mode": "report_only"})
    body = client.get("/api/live/status").json()
    assert body["llm_provider"] == "mock"
    assert body["ai_cost_estimate_today"] == 0.0
    assert body["ai_calls_today"] > 0
    assert "latest_candidates" in body
    assert "latest_order_intents" in body


def test_read_endpoints_do_not_mutate(tmp_path):
    # 시작 전 read 엔드포인트는 후보/intent를 만들지 않는다(읽기 전용).
    for _ in range(3):
        assert client.get("/api/live/candidates").json() == []
        assert client.get("/api/live/order-intents").json() == []
        b = client.get("/api/ai/status").json()
        assert b["ai_calls_today"] == 0  # read가 LLM을 호출하지 않음
