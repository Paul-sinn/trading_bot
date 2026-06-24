"""장중 오케스트레이터 v1 테스트 — 승인 요청만 생성(주문 없음).

검증: 장마감 skip · 장중 실행 · 대기 승인/일일 승인 캡/일일 실주문 캡/ stale 스냅샷 차단 ·
Discord 봇 미설정 차단 · 라우터 선택 시 승인 요청 생성 · 라우터 차단 이벤트 기록 · start/stop 상태 ·
API 주문 없음 · Robinhood write 미사용.

spec: specs/real_order_v1_checklist.md §12
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.order_router import OrderRouterResult
from backend.app.services.real_order_executor import RealExecutionReceipt, append_execution_receipt
from backend.app.services.approval_store import ApprovalRequest, append_request, compute_preview_hash, get_request
import backend.app.services.market_hours_orchestrator as mo
from backend.app.services.market_hours_orchestrator import MarketHoursOrchestrator
from backend.app.main import app

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)  # 평일 장중
LIVE = Settings().live_strategy_id


def _settings(**kw) -> Settings:
    base = dict(orchestrator_market_hours_only=True, orchestrator_require_fresh_broker_snapshot=False,
                orchestrator_require_discord_approval_worker=False, orchestrator_max_approvals_per_day=1,
                max_real_orders_per_day=1, order_router_daily_max_approval_requests=1,
                market_data_provider="mock")  # hermetic: 통합 라우터가 Alpaca 네트워크를 타지 않게
    base.update(kw)
    return Settings(**base)


def _orch(tmp_path) -> MarketHoursOrchestrator:
    return MarketHoursOrchestrator(reports_dir=tmp_path)


def _selected() -> OrderRouterResult:
    return OrderRouterResult(decision="ROUTER_SELECTED", reason="ok", approval_id="abc123", candidates_considered=1)


def _blocked() -> OrderRouterResult:
    return OrderRouterResult(decision="ROUTER_BLOCKED", reason="자격 후보 없음", block_reasons=["없음"], candidates_considered=0)


def _snap(ts=NOW) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=ts.isoformat(), account_last4="••••9372", buying_power=985.97,
                          positions=[], open_orders=[],
                          quotes=[{"symbol": "F", "price": 14.01, "bid": 14.0, "ask": 14.01, "as_of": ts.isoformat()}])


def _intent(symbol="F", key="s|F") -> OrderIntent:
    return OrderIntent(timestamp=NOW.isoformat(), session_id="s1", trading_mode="report_only", strategy_id=LIVE,
                       symbol=symbol, side="BUY", scan_event_key=key, mock_llm_decision="approve",
                       mock_llm_confidence=0.9, mock_llm_reason="ok", execution_gate_status="accepted_dry_run",
                       planned_order_type="limit", planned_limit_price=14.0, planned_notional_usd=50.0,
                       planned_quantity=50.0 / 14.0)


def _pending_req(tmp_path):
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="F", side="BUY", order_type="limit", notional=50.0,
                          source_intent_id="p|F", strategy_id=LIVE, idempotency_key="p|F",
                          preview_hash="x", status="PENDING")
    append_request(req, reports_dir=tmp_path)
    return req


def _run(orch, *, settings=None, market_open=True, router_fn=None, scan_fn=lambda: None):
    return orch.run_once(settings=settings or _settings(), now=NOW, market_open=market_open,
                         scan_fn=scan_fn, router_fn=router_fn or _blocked)


# --- 장시간 ---
def test_market_closed_skips(tmp_path):
    ev = _run(_orch(tmp_path), market_open=False)
    assert ev.action == "skip" and ev.result == "market_closed" and ev.real_orders_placed == 0


def test_market_open_runs_router(tmp_path):
    ev = _run(_orch(tmp_path), market_open=True, router_fn=_blocked)
    assert ev.action == "router_blocked" and ev.router_decision == "ROUTER_BLOCKED"


# --- 스냅샷 ---
def test_stale_snapshot_skips(tmp_path):
    append_snapshot(_snap(ts=NOW - timedelta(seconds=7200)), reports_dir=tmp_path)
    ev = _run(_orch(tmp_path), settings=_settings(orchestrator_require_fresh_broker_snapshot=True))
    assert ev.action == "skip" and ev.result == "snapshot_stale"


def test_missing_snapshot_skips(tmp_path):
    ev = _run(_orch(tmp_path), settings=_settings(orchestrator_require_fresh_broker_snapshot=True))
    assert ev.action == "skip" and ev.result == "snapshot_missing"


# --- 캡/대기 ---
def test_daily_real_cap_skips(tmp_path):
    append_execution_receipt(
        RealExecutionReceipt(intent_id="x", idempotency_key="x", symbol="F", side="BUY", decision="REAL_SUBMITTED",
                             environment="production", market_hours_source="real", is_proof_run=False,
                             broker_order_id="RH-1", real_order_placed=True, real_orders_placed=1,
                             timestamp=NOW.isoformat()),
        reports_dir=tmp_path)
    ev = _run(_orch(tmp_path))
    assert ev.action == "skip" and ev.result == "daily_real_cap"


def test_daily_approval_cap_skips(tmp_path):
    _pending_req(tmp_path)  # 오늘 생성된 요청 1건 → 캡 1 도달
    ev = _run(_orch(tmp_path), settings=_settings(orchestrator_max_approvals_per_day=1))
    assert ev.action == "skip" and ev.result == "daily_approval_cap"


def test_pending_approval_skips(tmp_path):
    req = _pending_req(tmp_path)
    # 승인 캡은 높이고 대기 승인 게이트만 검증
    ev = _run(_orch(tmp_path), settings=_settings(orchestrator_max_approvals_per_day=5))
    assert ev.action == "skip" and ev.result == "approval_pending" and ev.approval_id == req.approval_id


# --- Discord 봇 미설정 ---
def test_discord_worker_missing_warns(tmp_path):
    s = _settings(orchestrator_require_discord_approval_worker=True, discord_bot_token=None,
                  discord_approval_channel_id=None, discord_allowed_user_ids="")
    ev = _run(_orch(tmp_path), settings=s, router_fn=_selected)
    assert ev.action == "warn" and ev.result == "discord_worker_not_ready" and ev.approval_id is None


def test_discord_worker_present_allows(tmp_path):
    s = _settings(orchestrator_require_discord_approval_worker=True, discord_bot_token="t",
                  discord_approval_channel_id="123", discord_allowed_user_ids="U1")
    ev = _run(_orch(tmp_path), settings=s, router_fn=_selected)
    assert ev.action == "approval_requested" and ev.approval_id == "abc123"


# --- 라우터 결과 반영 ---
def test_router_selected_creates_approval_event(tmp_path):
    ev = _run(_orch(tmp_path), router_fn=_selected)
    assert ev.action == "approval_requested" and ev.result == "selected"
    assert ev.router_decision == "ROUTER_SELECTED" and ev.approval_id == "abc123"
    assert ev.real_orders_placed == 0


def test_router_blocked_records_event(tmp_path):
    orch = _orch(tmp_path)
    _run(orch, router_fn=_blocked)
    evs = mo.load_events(reports_dir=tmp_path)
    assert evs and evs[-1].action == "router_blocked" and evs[-1].result == "blocked"


# --- 실제 라우터 통합(승인 요청 생성) ---
def test_integration_real_router_creates_request(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    (tmp_path / "live_order_intents.jsonl").write_text(
        _intent().model_dump_json() + "\n", encoding="utf-8")
    s = _settings(orchestrator_require_fresh_broker_snapshot=True)
    ev = _orch(tmp_path).run_once(settings=s, now=NOW, market_open=True, scan_fn=lambda: None)  # 실 라우터
    assert ev.action == "approval_requested" and ev.approval_id
    req = get_request(ev.approval_id, reports_dir=tmp_path)
    assert req is not None and req.type == "BUY" and req.broker_order_id is None and req.notional <= 100.0


# --- start/stop 상태 ---
def test_start_stop_updates_state(tmp_path):
    orch = _orch(tmp_path)
    assert orch.running is False
    r = orch.start(settings=_settings(orchestrator_interval_seconds=3600))
    assert r["started"] is True and orch.running is True
    orch.stop()
    assert orch.running is False


# --- API ---
def test_api_orchestrator_status_and_events(tmp_path, monkeypatch):
    monkeypatch.setattr(mo, "DEFAULT_REPORTS_DIR", tmp_path)
    mo.set_orchestrator(MarketHoursOrchestrator(reports_dir=tmp_path))
    c = TestClient(app)
    status = c.get("/api/live/orchestrator/status").json()
    assert status["enabled"] is False and status["real_orders_placed"] == 0 and status["running"] is False
    evs = c.get("/api/live/orchestrator/events?limit=10").json()
    assert isinstance(evs, list)
    mo.set_orchestrator(None)


def test_api_run_once_no_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(mo, "DEFAULT_REPORTS_DIR", tmp_path)
    mo.set_orchestrator(MarketHoursOrchestrator(reports_dir=tmp_path))
    body = TestClient(app).post("/api/live/orchestrator/run-once").json()
    assert body["real_orders_placed"] == 0
    # 어떤 실행 영수증도 만들지 않는다(주문 없음).
    assert not (tmp_path / "real_execution_receipts.jsonl").exists()
    mo.set_orchestrator(None)


# --- Robinhood write 미사용 ---
def test_no_robinhood_write_tool_in_orchestrator():
    import inspect
    text = inspect.getsource(mo)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text


def test_cli_no_robinhood():
    from pathlib import Path
    text = Path("scripts/run_market_orchestrator.py").read_text(encoding="utf-8")
    assert "mcp__robinhood" not in text and "place_equity_order" not in text
