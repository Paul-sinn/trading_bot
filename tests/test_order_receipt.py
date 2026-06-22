"""OrderIntent 워커 계약 v0 — dry-run 영수증 테스트.

spec: specs/broker_snapshot_bridge.md
검증: 멱등(중복 skip), emergency_halt/block_new_orders/stale/missing snapshot/buying_power 부족/
중복 미체결 매수 → BLOCKED, 전부 통과 시에만 WOULD_SUBMIT. 항상 broker_order_id=None,
real_order_placed=False, real_orders_placed=0. API 읽기 전용. 시크릿/주문 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.candidate_pipeline import ORDER_INTENTS_LOG
from backend.app.services.control_flags import ControlFlags, write_control_flags
from backend.app.services.execution_gate import OrderIntent
import backend.app.services.order_receipt as orec
from backend.app.services.order_receipt import (
    OrderReceipt,
    evaluate_intent,
    load_receipts,
    process_pending_intents,
)
from backend.app.main import app


def _intent(symbol="NVDA", notional=1000.0, key="s1|NVDA|2026-06-22|strat") -> OrderIntent:
    return OrderIntent(
        timestamp="2026-06-22T21:00:00+00:00",
        session_id="s1",
        trading_mode="report_only",
        strategy_id="strat",
        symbol=symbol,
        side="BUY",
        scan_event_key=key,
        mock_llm_decision="approve",
        mock_llm_confidence=0.9,
        mock_llm_reason="ok",
        execution_gate_status="accepted_dry_run",
        planned_limit_price=100.0,
        planned_notional_usd=notional,
        planned_quantity=notional / 100.0,
    )


def _running_flags() -> ControlFlags:
    return ControlFlags(
        automation_running=True,
        emergency_halt=False,
        block_new_orders=False,
        block_new_llm_calls=False,
    )


def _fresh_snap(buying_power=5000.0, open_orders=None) -> BrokerSnapshot:
    return BrokerSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
        buying_power=buying_power,
        open_orders=open_orders or [],
    )


# --- 영수증 불변식 ---
def test_receipt_invariants_forced():
    r = OrderReceipt(intent_id="i", idempotency_key="i", symbol="NVDA", decision="WOULD_SUBMIT")
    assert r.broker_order_id is None
    assert r.real_order_placed is False
    assert r.real_orders_placed == 0
    assert r.mode == "dry_run_receipt_only"


# --- evaluate_intent 결정 매트릭스 ---
def test_would_submit_when_all_checks_pass():
    r = evaluate_intent(_intent(), control_flags=_running_flags(), snapshot=_fresh_snap())
    assert r.decision == "WOULD_SUBMIT"
    assert "would have been submitted" in r.reason
    assert r.control_flags_checked and r.broker_snapshot_checked
    assert r.broker_order_id is None and r.real_order_placed is False and r.real_orders_placed == 0


def test_missing_control_flags_blocks():
    r = evaluate_intent(_intent(), control_flags=None, snapshot=_fresh_snap())
    assert r.decision == "BLOCKED" and "control_flags" in r.reason


def test_emergency_halt_blocks():
    f = _running_flags().model_copy(update={"emergency_halt": True})
    r = evaluate_intent(_intent(), control_flags=f, snapshot=_fresh_snap())
    assert r.decision == "BLOCKED" and "emergency_halt" in r.reason


def test_block_new_orders_blocks():
    f = _running_flags().model_copy(update={"block_new_orders": True})
    r = evaluate_intent(_intent(), control_flags=f, snapshot=_fresh_snap())
    assert r.decision == "BLOCKED" and "block_new_orders" in r.reason


def test_missing_snapshot_blocks():
    r = evaluate_intent(_intent(), control_flags=_running_flags(), snapshot=None)
    assert r.decision == "BLOCKED" and "snapshot 없음" in r.reason


def test_insufficient_buying_power_blocks():
    r = evaluate_intent(_intent(notional=1000.0), control_flags=_running_flags(), snapshot=_fresh_snap(buying_power=500.0))
    assert r.decision == "BLOCKED" and "buying_power" in r.reason


def test_duplicate_open_buy_blocks():
    snap = _fresh_snap(open_orders=[{"symbol": "NVDA", "side": "buy", "state": "new"}])
    r = evaluate_intent(_intent(), control_flags=_running_flags(), snapshot=snap)
    assert r.decision == "BLOCKED" and "중복" in r.reason


def test_stale_snapshot_warns_by_default():
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    snap = BrokerSnapshot(timestamp=old, buying_power=5000.0)
    r = evaluate_intent(_intent(), control_flags=_running_flags(), snapshot=snap, max_snapshot_age_seconds=3600)
    assert r.decision == "WOULD_SUBMIT"
    assert any("stale" in e for e in r.errors)


def test_stale_snapshot_rejects_when_configured():
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    snap = BrokerSnapshot(timestamp=old, buying_power=5000.0)
    r = evaluate_intent(
        _intent(), control_flags=_running_flags(), snapshot=snap,
        max_snapshot_age_seconds=3600, reject_on_stale_snapshot=True,
    )
    assert r.decision == "BLOCKED" and "stale" in r.reason


# --- process_pending_intents: 멱등 + 저장 ---
def _seed(reports_dir, *intents: OrderIntent) -> None:
    path = reports_dir / ORDER_INTENTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for it in intents:
            fh.write(it.model_dump_json() + "\n")


def test_process_writes_would_submit_and_is_idempotent(tmp_path):
    _seed(tmp_path, _intent())
    write_control_flags(_running_flags(), reports_dir=tmp_path)
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)

    first = process_pending_intents(reports_dir=tmp_path, settings=Settings())
    assert len(first) == 1 and first[0].decision == "WOULD_SUBMIT"

    # 두 번째 실행: 같은 idempotency_key → 새 영수증 미발행(멱등).
    second = process_pending_intents(reports_dir=tmp_path, settings=Settings())
    assert second == []
    assert len(load_receipts(reports_dir=tmp_path)) == 1  # 파일에 1건만


def test_process_blocks_when_halted(tmp_path):
    _seed(tmp_path, _intent())
    write_control_flags(_running_flags().model_copy(update={"emergency_halt": True}), reports_dir=tmp_path)
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)
    out = process_pending_intents(reports_dir=tmp_path, settings=Settings())
    assert len(out) == 1 and out[0].decision == "BLOCKED"
    assert out[0].real_orders_placed == 0 and out[0].broker_order_id is None


def test_no_full_account_or_secret_in_receipts(tmp_path):
    _seed(tmp_path, _intent())
    write_control_flags(_running_flags(), reports_dir=tmp_path)
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)
    process_pending_intents(reports_dir=tmp_path, settings=Settings())
    raw = (tmp_path / "live_order_receipts.jsonl").read_text(encoding="utf-8")
    for needle in ("778689372", "516530169", "token", "bearer", "secret", "authorization"):
        assert needle.lower() not in raw.lower()
    assert '"real_orders_placed": 0' in raw
    assert '"real_order_placed": false' in raw
    assert '"broker_order_id": null' in raw


# --- API 읽기 전용 ---
@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(orec, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_api_receipts_empty(reports):
    client = TestClient(app)
    assert client.get("/api/live/order-receipts").json() == []
    assert client.get("/api/live/order-receipts/latest").json() is None


def test_api_receipts_returns_written(reports):
    _seed(reports, _intent())
    write_control_flags(_running_flags(), reports_dir=reports)
    append_snapshot(_fresh_snap(), reports_dir=reports)
    process_pending_intents(reports_dir=reports, settings=Settings())

    client = TestClient(app)
    body = client.get("/api/live/order-receipts").json()
    assert len(body) == 1
    assert body[0]["decision"] == "WOULD_SUBMIT"
    assert body[0]["broker_order_id"] is None
    assert body[0]["real_order_placed"] is False
    assert body[0]["real_orders_placed"] == 0

    latest = client.get("/api/live/order-receipts/latest").json()
    assert latest["symbol"] == "NVDA" and latest["mode"] == "dry_run_receipt_only"
