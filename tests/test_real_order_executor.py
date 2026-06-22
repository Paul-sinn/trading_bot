"""실주문 실행 워커 v1 scaffold 테스트 — 기본 비활성, 실주문 없음.

spec: specs/broker_snapshot_bridge.md
검증: 기본 config·arm 부재/만료/손상·notional 초과·stale·buying_power 부족·중복 매수·sell/옵션·
멱등 → REAL_BLOCKED. 전부 통과 + mock executor → MOCK_SUBMITTED(가짜 id). 항상 real_order_placed=
False, real_orders_placed=0. 실 Robinhood write 도구는 호출 불가(RealExecutionDisabled). API 읽기 전용.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_arm import RealOrderArm, write_arm
import backend.app.services.real_order_executor as rex
from backend.app.services.real_order_executor import (
    MockOrderExecutor,
    RealExecutionDisabled,
    RealRobinhoodOrderExecutor,
    build_receipt,
    evaluate_readiness,
    process_execution,
)
from backend.app.main import app

NOW = datetime(2026, 6, 22, 15, 0, 0, tzinfo=timezone.utc)  # 평일 15:00 UTC = 장중


def _intent(symbol="AAPL", notional=10.0, side="BUY", order_type="limit", key="s1|AAPL|2026-06-22|strat") -> OrderIntent:
    return OrderIntent(
        timestamp="2026-06-22T14:00:00+00:00", session_id="s1", trading_mode="report_only",
        strategy_id="strat", symbol=symbol, side=side, scan_event_key=key,
        mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="ok",
        execution_gate_status="accepted_dry_run", planned_order_type=order_type,
        planned_limit_price=100.0, planned_notional_usd=notional, planned_quantity=(notional / 100.0),
    )


def _enabled() -> Settings:
    # 실행을 허용하는 설정(소액 cap). 그래도 실 제출 경로는 없음.
    return Settings(enable_real_order_execution=True, max_notional_per_real_order_usd=25.0, max_real_orders_per_day=1)


def _good_arm() -> RealOrderArm:
    return RealOrderArm(
        armed=True, armed_at=NOW.isoformat(), expires_at=(NOW + timedelta(seconds=120)).isoformat(),
        max_notional=25.0, reason="rehearsal", created_by="test",
    )


def _fresh_snap(bp=1000.0, open_orders=None) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=NOW.isoformat(), buying_power=bp, open_orders=open_orders or [])


def _ready(intent, settings, arm, snapshot, **kw):
    return evaluate_readiness(
        intent, settings=settings, arm=arm, snapshot=snapshot,
        daily_real_count=kw.get("daily_real_count", 0), executed_keys=kw.get("executed_keys", set()),
        now=NOW, market_open=kw.get("market_open", True),
    )


# --- 실 executor는 절대 제출하지 않는다 ---
def test_real_executor_always_raises():
    with pytest.raises(RealExecutionDisabled):
        RealRobinhoodOrderExecutor().submit_limit_buy(symbol="AAPL", quantity=1, limit_price=10)


# --- 차단 매트릭스 ---
def test_default_config_blocks():
    r = _ready(_intent(), Settings(), _good_arm(), _fresh_snap())
    assert not r.ready
    assert any("ENABLE_REAL_ORDER_EXECUTION=false" in x for x in r.block_reasons)


def test_missing_arm_blocks():
    r = _ready(_intent(), _enabled(), None, _fresh_snap())
    assert not r.ready and any("arm missing" in x for x in r.block_reasons)


def test_expired_arm_blocks():
    arm = _good_arm().model_copy(update={"expires_at": (NOW - timedelta(seconds=1)).isoformat()})
    r = _ready(_intent(), _enabled(), arm, _fresh_snap())
    assert not r.ready and any("arm expired" in x for x in r.block_reasons)


def test_disarmed_blocks():
    r = _ready(_intent(), _enabled(), _good_arm().model_copy(update={"armed": False}), _fresh_snap())
    assert not r.ready and any("arm disarmed" in x for x in r.block_reasons)


def test_malformed_arm_file_blocks(tmp_path):
    (tmp_path / "real_order_arm.json").write_text("not json", encoding="utf-8")
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)
    rcpt = process_execution(_intent(), settings=_enabled(), reports_dir=tmp_path, now=NOW, market_open=True)
    assert rcpt.decision == "REAL_BLOCKED" and any("arm missing" in x for x in rcpt.block_reasons)


def test_notional_over_cap_blocks():
    r = _ready(_intent(notional=50.0), _enabled(), _good_arm(), _fresh_snap())
    assert not r.ready and any("MAX_NOTIONAL_PER_REAL_ORDER" in x for x in r.block_reasons)


def test_stale_snapshot_blocks():
    stale = BrokerSnapshot(timestamp=(NOW - timedelta(seconds=7200)).isoformat(), buying_power=1000.0)
    r = _ready(_intent(), _enabled(), _good_arm(), stale)
    assert not r.ready and any("stale" in x for x in r.block_reasons)


def test_insufficient_buying_power_blocks():
    r = _ready(_intent(notional=20.0), _enabled(), _good_arm(), _fresh_snap(bp=5.0))
    assert not r.ready and any("buying_power" in x for x in r.block_reasons)


def test_duplicate_open_buy_blocks():
    snap = _fresh_snap(open_orders=[{"symbol": "AAPL", "side": "buy", "state": "new"}])
    r = _ready(_intent(), _enabled(), _good_arm(), snap)
    assert not r.ready and any("중복" in x for x in r.block_reasons)


def test_sell_blocked():
    r = _ready(_intent(side="SELL"), _enabled(), _good_arm(), _fresh_snap())
    assert not r.ready and any("sell" in x.lower() for x in r.block_reasons)


def test_non_limit_blocked():
    r = _ready(_intent(order_type="market"), _enabled(), _good_arm(), _fresh_snap())
    assert not r.ready and any("limit buy only" in x for x in r.block_reasons)


def test_arm_symbol_mismatch_blocks():
    arm = _good_arm().model_copy(update={"allowed_symbol": "MSFT"})
    r = _ready(_intent(symbol="AAPL"), _enabled(), arm, _fresh_snap())
    assert not r.ready and any("allowed_symbol" in x for x in r.block_reasons)


def test_outside_market_hours_blocks():
    r = _ready(_intent(), _enabled(), _good_arm(), _fresh_snap(), market_open=False)
    assert not r.ready and any("장시간" in x for x in r.block_reasons)


def test_daily_cap_blocks():
    r = _ready(_intent(), _enabled(), _good_arm(), _fresh_snap(), daily_real_count=1)
    assert not r.ready and any("MAX_REAL_ORDERS_PER_DAY" in x for x in r.block_reasons)


def test_idempotency_blocks():
    r = _ready(_intent(key="dup"), _enabled(), _good_arm(), _fresh_snap(), executed_keys={"dup"})
    assert not r.ready and any("idempotency" in x for x in r.block_reasons)


# --- 통과 경로 ---
def test_all_pass_real_ready_dry_run_no_submission():
    r = _ready(_intent(), _enabled(), _good_arm(), _fresh_snap())
    assert r.ready and r.block_reasons == []
    rcpt = build_receipt(_intent(), r, executor=None)  # 실 executor 기본 → 제출 없음
    assert rcpt.decision == "REAL_READY_DRY_RUN"
    assert rcpt.broker_order_id is None
    assert rcpt.real_order_placed is False and rcpt.real_orders_placed == 0


def test_mock_submitted_returns_fake_id_no_real_order():
    r = _ready(_intent(), _enabled(), _good_arm(), _fresh_snap())
    rcpt = build_receipt(_intent(), r, executor=MockOrderExecutor())
    assert rcpt.decision == "MOCK_SUBMITTED"
    assert rcpt.broker_order_id and rcpt.broker_order_id.startswith("MOCK-")
    assert rcpt.real_order_placed is False and rcpt.real_orders_placed == 0  # mock여도 실주문 아님


def test_receipt_invariants_forced():
    r = _ready(_intent(), _enabled(), _good_arm(), _fresh_snap())
    rcpt = build_receipt(_intent(), r, executor=MockOrderExecutor())
    # 강제 불변식: 어떤 경로로도 실주문 흔적 0.
    assert rcpt.real_order_placed is False
    assert rcpt.real_orders_placed == 0
    assert rcpt.mode == "real_execution_scaffold"


# --- process_execution 저장 + 멱등(MOCK 제출 후 재실행 시 idempotency 차단) ---
def test_process_execution_persists_and_blocks_default(tmp_path):
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)
    write_arm(_good_arm(), reports_dir=tmp_path)
    # 기본 Settings(비활성) → REAL_BLOCKED, 파일 적재.
    rcpt = process_execution(_intent(), settings=Settings(), reports_dir=tmp_path, now=NOW, market_open=True)
    assert rcpt.decision == "REAL_BLOCKED"
    raw = (tmp_path / "real_execution_receipts.jsonl").read_text(encoding="utf-8")
    assert '"real_order_placed": false' in raw and '"real_orders_placed": 0' in raw


def test_no_secrets_or_full_account_in_receipts(tmp_path):
    append_snapshot(_fresh_snap(), reports_dir=tmp_path)
    write_arm(_good_arm(), reports_dir=tmp_path)
    process_execution(_intent(), settings=_enabled(), reports_dir=tmp_path, now=NOW, market_open=True)
    raw = (tmp_path / "real_execution_receipts.jsonl").read_text(encoding="utf-8").lower()
    for needle in ("778689372", "516530169", "token", "bearer", "secret", "authorization"):
        assert needle not in raw


# --- API 읽기 전용 ---
@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(rex, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_api_execution_status_default_disabled(reports):
    client = TestClient(app)
    body = client.get("/api/live/execution-status").json()
    assert body["real_execution_enabled"] is False
    assert body["arm_status"] == "missing"
    assert body["max_notional_per_real_order_usd"] == 25.0
    assert body["real_orders_today"] == 0
    assert body["real_orders_placed"] == 0
