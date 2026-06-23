"""수동 확인 매도 워커 v0 scaffold 테스트 — 기본 비활성, 실 매도 없음.

spec: specs/real_order_v1_checklist.md
검증: arm 부재/만료/손상/심볼불일치 차단 · 포지션 없음/수량초과/중복매도 차단 · 장마감 차단 ·
전부 통과 시 mocked 경로로만 SELL_READY_DRY_RUN(test) · mocked proof는 production latest에 미노출 ·
실 매도 executor 항상 disabled · broker_order_id=null · real_sell_order_placed=false · write 도구 미사용.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.control_flags import ControlFlags, write_control_flags
from backend.app.services.execution_gate import OrderIntent
import backend.app.services.real_sell_executor as se
from backend.app.services.real_sell_executor import (
    RealSellExecutionDisabled,
    RealRobinhoodSellExecutor,
    build_sell_receipt,
    evaluate_sell_readiness,
    process_sell_execution,
)
from backend.app.services.real_sell_arm import RealSellArm, write_sell_arm
from backend.app.main import app

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)  # 평일 장중


def _intent(symbol="F", qty=1.0, limit=14.0, order_type="limit", side="SELL", key="s|F|sell") -> OrderIntent:
    return OrderIntent(
        timestamp="2026-06-23T14:00:00+00:00", session_id="s", trading_mode="report_only",
        strategy_id="manual-sell", symbol=symbol, side=side, scan_event_key=key,
        mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="manual sell",
        execution_gate_status="accepted_dry_run", planned_order_type=order_type,
        planned_limit_price=limit, planned_notional_usd=limit * qty, planned_quantity=qty,
    )


def _enabled() -> Settings:
    return Settings(allow_real_sell_orders=True, agentic_account_only=True,
                    require_market_hours_for_real_order=True, require_fresh_broker_snapshot_for_real_order=True)


def _arm(**kw) -> RealSellArm:
    base = dict(armed=True, armed_at=NOW.isoformat(), expires_at=(NOW + timedelta(seconds=120)).isoformat(),
                allowed_symbol="F", max_quantity=1.0, reason="manual sell rehearsal", created_by="test")
    base.update(kw)
    return RealSellArm(**base)


def _snap(positions=None, open_orders=None) -> BrokerSnapshot:
    if positions is None:
        positions = [{"symbol": "F", "quantity": 1.0, "average_buy_price": 14.03, "shares_available_for_sells": 1.0}]
    return BrokerSnapshot(timestamp=NOW.isoformat(), account_last4="••••9372", buying_power=985.97,
                          positions=positions, open_orders=open_orders or [])


def _flags() -> ControlFlags:
    return ControlFlags(automation_running=True, emergency_halt=False, block_new_orders=False, block_new_llm_calls=False)


def _ready(intent, settings, arm, snap, **kw):
    return evaluate_sell_readiness(
        intent, settings=settings, arm=arm, snapshot=snap, sold_keys=kw.get("sold_keys", set()),
        control_flags=kw.get("control_flags", _flags()), now=NOW, market_open=kw.get("market_open", True),
    )


# --- 실 매도 executor 항상 disabled ---
def test_real_sell_executor_always_raises():
    with pytest.raises(RealSellExecutionDisabled):
        RealRobinhoodSellExecutor().submit_limit_sell(symbol="F", quantity=1, limit_price=14)


# --- 차단 매트릭스 ---
def test_default_disabled_blocks():
    r = _ready(_intent(), Settings(), _arm(), _snap())  # allow_real_sell_orders=False
    assert not r.ready and any("ALLOW_REAL_SELL_ORDERS=false" in x for x in r.block_reasons)


def test_missing_arm_blocks():
    r = _ready(_intent(), _enabled(), None, _snap())
    assert not r.ready and any("sell arm missing" in x for x in r.block_reasons)


def test_expired_arm_blocks():
    r = _ready(_intent(), _enabled(), _arm(expires_at=(NOW - timedelta(seconds=1)).isoformat()), _snap())
    assert not r.ready and any("sell arm expired" in x for x in r.block_reasons)


def test_disarmed_blocks():
    r = _ready(_intent(), _enabled(), _arm(armed=False), _snap())
    assert not r.ready and any("sell arm disarmed" in x for x in r.block_reasons)


def test_malformed_arm_file_blocks(tmp_path):
    (tmp_path / "real_sell_arm.json").write_text("not json", encoding="utf-8")
    append_snapshot(_snap(), reports_dir=tmp_path)
    write_control_flags(_flags(), reports_dir=tmp_path)
    r = process_sell_execution(_intent(), settings=_enabled(), reports_dir=tmp_path, now=NOW, market_open=True)
    assert r.decision == "SELL_BLOCKED" and any("sell arm missing" in x for x in r.block_reasons)


def test_arm_symbol_mismatch_blocks():
    r = _ready(_intent(symbol="F"), _enabled(), _arm(allowed_symbol="AAPL"), _snap())
    assert not r.ready and any("allowed_symbol" in x for x in r.block_reasons)


def test_no_position_blocks():
    r = _ready(_intent(symbol="F"), _enabled(), _arm(), _snap(positions=[]))
    assert not r.ready and any("매도할 포지션 없음" in x for x in r.block_reasons)


def test_quantity_over_available_blocks():
    snap = _snap(positions=[{"symbol": "F", "quantity": 1.0, "average_buy_price": 14.03, "shares_available_for_sells": 1.0}])
    r = _ready(_intent(qty=2.0), _enabled(), _arm(max_quantity=5.0), snap)
    assert not r.ready and any("매도가능수량" in x for x in r.block_reasons)


def test_duplicate_open_sell_blocks():
    snap = _snap(open_orders=[{"symbol": "F", "side": "sell", "state": "new"}])
    r = _ready(_intent(), _enabled(), _arm(), snap)
    assert not r.ready and any("중복 미체결 매도" in x for x in r.block_reasons)


def test_non_limit_blocks():
    r = _ready(_intent(order_type="market"), _enabled(), _arm(), _snap())
    assert not r.ready and any("limit sell only" in x for x in r.block_reasons)


def test_market_closed_blocks():
    r = _ready(_intent(), _enabled(), _arm(), _snap(), market_open=False)
    assert not r.ready and any("장시간 아님" in x for x in r.block_reasons)


def test_emergency_halt_blocks():
    r = _ready(_intent(), _enabled(), _arm(), _snap(), control_flags=_flags().model_copy(update={"emergency_halt": True}))
    assert not r.ready and any("emergency_halt" in x for x in r.block_reasons)


def test_block_new_orders_blocks():
    r = _ready(_intent(), _enabled(), _arm(), _snap(), control_flags=_flags().model_copy(update={"block_new_orders": True}))
    assert not r.ready and any("block_new_orders" in x for x in r.block_reasons)


def test_missing_control_flags_blocks():
    r = _ready(_intent(), _enabled(), _arm(), _snap(), control_flags=None)
    assert not r.ready and any("control_flags 없음" in x for x in r.block_reasons)


def test_idempotency_blocks():
    r = _ready(_intent(key="dup"), _enabled(), _arm(), _snap(), sold_keys={"dup"})
    assert not r.ready and any("idempotency" in x for x in r.block_reasons)


# --- 통과 경로(모의 시장시간 → test/proof) ---
def test_valid_path_reaches_ready_dry_run_test_env():
    r = _ready(_intent(), _enabled(), _arm(), _snap())
    assert r.ready and r.block_reasons == []
    rcpt = build_sell_receipt(_intent(), r, market_hours_source="mocked")
    assert rcpt.decision == "SELL_READY_DRY_RUN"
    assert rcpt.environment == "test" and rcpt.is_proof_run is True
    assert rcpt.broker_order_id is None
    assert rcpt.real_sell_order_placed is False and rcpt.real_sell_orders_placed == 0
    assert rcpt.real_order_placed is False and rcpt.real_orders_placed == 0  # 매수 카운터 불변


def test_receipt_invariants_forced():
    r = _ready(_intent(), _enabled(), _arm(), _snap())
    rcpt = build_sell_receipt(_intent(), r, market_hours_source="mocked")
    assert rcpt.broker_order_id is None
    assert rcpt.real_sell_order_placed is False and rcpt.real_sell_orders_placed == 0


# --- production latest는 mocked proof를 무시 ---
@pytest.fixture
def reports(tmp_path, monkeypatch):
    import backend.app.services.broker_snapshot as bs
    import backend.app.services.real_sell_arm as arm_mod
    monkeypatch.setattr(se, "DEFAULT_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(bs, "DEFAULT_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(arm_mod, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_mocked_proof_not_production_latest(reports):
    append_snapshot(_snap(), reports_dir=reports)
    write_sell_arm(_arm(), reports_dir=reports)
    write_control_flags(_flags(), reports_dir=reports)
    # 모의 시장시간 proof만 기록 → environment=test
    rcpt = process_sell_execution(_intent(key="proof"), settings=_enabled(), reports_dir=reports, now=NOW, market_open=True)
    assert rcpt.environment == "test" and rcpt.decision == "SELL_READY_DRY_RUN"

    body = TestClient(app).get("/api/live/sell-execution-status").json()
    assert body["latest_decision"] is None  # 프로덕션 매도 receipt 없음
    assert body["allow_real_sell_orders"] is False  # 기본 Settings(.env 무관 — 테스트)


def test_api_sell_status_default(reports):
    append_snapshot(_snap(), reports_dir=reports)
    body = TestClient(app).get("/api/live/sell-execution-status").json()
    assert body["allow_real_sell_orders"] is False
    assert body["sell_arm_status"] == "missing"
    assert body["real_sell_orders_placed"] == 0
    assert any(p["symbol"] == "F" for p in body["sellable_positions"])


# --- write 도구 미사용 ---
def test_no_robinhood_write_tool_imported():
    import inspect
    text = inspect.getsource(se)
    assert "mcp__robinhood" not in text
    with pytest.raises(RealSellExecutionDisabled):
        RealRobinhoodSellExecutor().submit_limit_sell(symbol="F", quantity=1, limit_price=14)
