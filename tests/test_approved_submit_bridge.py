"""승인 Robinhood MCP 제출 브리지 v1 테스트 — 실주문 없음, mock/주입 콜백만.

검증: RobinhoodMcpBuyExecutor는 기본 disabled(FastAPI 직접 사용 불가) · 워커 컨텍스트+주입 콜백에서만
제출 콜백 호출 · 승인 실행 워커 execute-real 기본 fail-closed · mock/test 제출은 실 카운터 0 · submit_mode
기록 · limit/fractional 둘 다 · 멱등 중복 제출 차단 · 코드에 실 MCP write 도구명 미포함.

spec: specs/real_order_v1_checklist.md §15
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.approval_store import (
    ApprovalDecision,
    ApprovalRequest,
    append_decision,
    append_request,
    compute_preview_hash,
    create_approval_request,
)
import backend.app.services.real_order_executor as rex
from backend.app.services.real_order_executor import (
    MockOrderExecutor,
    RealExecutionDisabled,
    RobinhoodMcpBuyExecutor,
)
import backend.app.services.approved_execution as ae
from backend.app.services.approved_execution import process_approved_execution

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)
LIVE = Settings().live_strategy_id


def _settings(**kw) -> Settings:
    base = dict(discord_allowed_user_ids="U1", enable_real_order_execution=True,
                max_notional_per_real_order_usd=100.0, max_real_orders_per_day=1,
                require_market_hours_for_real_order=True, require_fresh_broker_snapshot_for_real_order=True,
                agentic_account_only=True, strategy_intent_only_for_real_order=True)
    base.update(kw)
    return Settings(**base)


def _snap(account="••••9372", bp=985.97) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=NOW.isoformat(), account_last4=account, buying_power=bp,
                          positions=[], open_orders=[])


def _intent(symbol="F", notional=50.0, limit=14.0, key="s|F") -> OrderIntent:
    return OrderIntent(timestamp=NOW.isoformat(), scan_run_id="s1", intent_generated_at=NOW.isoformat(),
                       trading_date=NOW.date().isoformat(), session_id="s1", trading_mode="report_only",
                       strategy_id=LIVE, symbol=symbol, side="BUY", scan_event_key=key,
                       mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="ok",
                       execution_gate_status="accepted_dry_run", planned_order_type="limit",
                       planned_limit_price=limit, planned_notional_usd=notional, planned_quantity=notional / limit)


def _approve_limit(tmp_path, settings):
    snap = _snap()
    append_snapshot(snap, reports_dir=tmp_path)
    req = create_approval_request(_intent(), type="BUY", settings=settings, snapshot=snap, now=NOW,
                                  reports_dir=tmp_path, send=False)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    return req


def _approve_market(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    ph = compute_preview_hash(type="BUY", symbol="NVDA", side="BUY", order_type="market", quantity=None,
                              limit_price=None, notional=100.0, account_last4="••••9372",
                              source_intent_id="s|NVDA", strategy_id=LIVE, idempotency_key="s|NVDA",
                              scan_run_id="s1", intent_generated_at=NOW.isoformat(),
                              trading_date=NOW.date().isoformat())
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="NVDA", side="BUY", order_type="market", quantity=None,
                          dollar_amount=100.0, limit_price=None, notional=100.0, account_last4="••••9372",
                          source_intent_id="s|NVDA", strategy_id=LIVE, idempotency_key="s|NVDA",
                          scan_run_id="s1", intent_generated_at=NOW.isoformat(),
                          trading_date=NOW.date().isoformat(),
                          preview_hash=ph, status="PENDING")
    append_request(req, reports_dir=tmp_path)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    return req


# --- 브리지 executor 단위 ---
def test_bridge_disabled_by_default():
    ex = RobinhoodMcpBuyExecutor()  # FastAPI 기본 — 워커 컨텍스트 아님
    with pytest.raises(RealExecutionDisabled):
        ex.submit_limit_buy(symbol="F", quantity=1, limit_price=14.0)
    with pytest.raises(RealExecutionDisabled):
        ex.submit_market_buy(symbol="NVDA", dollar_amount=100.0)


def test_bridge_disabled_without_submit_fn():
    ex = RobinhoodMcpBuyExecutor(worker_context=True, submit_fn=None)
    with pytest.raises(RealExecutionDisabled):
        ex.submit_limit_buy(symbol="F", quantity=1, limit_price=14.0)


def test_bridge_worker_context_invokes_callback():
    calls = []

    def fake(**kw):
        calls.append(kw)
        return {"broker_order_id": "BRIDGE-1", "symbol": kw.get("symbol")}

    ex = RobinhoodMcpBuyExecutor(worker_context=True, submit_fn=fake)
    assert ex.submit_limit_buy(symbol="F", quantity=2, limit_price=14.0)["broker_order_id"] == "BRIDGE-1"
    assert ex.submit_market_buy(symbol="NVDA", dollar_amount=100.0)["broker_order_id"] == "BRIDGE-1"
    assert calls[0]["kind"] == "limit" and calls[1]["kind"] == "market"


# --- 승인 실행 워커 통합 ---
def _run(tmp_path, settings, *, execute_real=False, executor=None, market_open=True, now=NOW):
    return process_approved_execution(settings=settings, reports_dir=tmp_path, now=now,
                                      market_open=market_open, execute_real=execute_real, executor=executor)


def test_dry_run_never_submits_records_mode(tmp_path):
    s = _settings()
    _approve_limit(tmp_path, s)
    r = _run(tmp_path, s, execute_real=False)
    assert r.decision == "APPROVED_READY_DRY_RUN" and r.submit_mode == "dry_run"
    assert r.broker_order_id is None and r.real_order_placed is False and r.real_orders_placed == 0


def test_execute_real_default_bridge_fail_closed(tmp_path):
    s = _settings()
    _approve_limit(tmp_path, s)
    # executor 미지정 → 기본 RobinhoodMcpBuyExecutor(워커 컨텍스트 아님) → fail-closed.
    r = process_approved_execution(settings=s, reports_dir=tmp_path, now=NOW, execute_real=True)  # real 시장시간
    assert r.decision == "BLOCKED" and r.submit_mode == "execute_real" and r.environment == "production"
    assert "fail-closed" in r.reason
    assert r.real_order_placed is False and r.real_orders_placed == 0 and r.broker_order_id is None


def test_execute_real_blocked_without_approval(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    r = _run(tmp_path, _settings(), execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "BLOCKED" and r.real_orders_placed == 0


def test_limit_mock_submit_counters_zero(tmp_path):
    s = _settings()
    _approve_limit(tmp_path, s)
    r = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "REAL_SUBMITTED" and r.order_type == "limit"
    assert r.broker_order_id and r.broker_order_id.startswith("MOCK-")
    assert r.environment == "test" and r.real_order_placed is False and r.real_orders_placed == 0


def test_fractional_market_mock_submit_counters_zero(tmp_path):
    s = _settings()
    _approve_market(tmp_path)
    r = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "REAL_SUBMITTED" and r.order_type == "market"
    assert r.broker_order_id and r.broker_order_id.startswith("MOCK-MKT-")
    assert r.dollar_amount == 100.0 and r.real_order_placed is False and r.real_orders_placed == 0


def test_bridge_worker_context_submit_via_worker_test_only(tmp_path):
    # 주입 콜백 + 워커 컨텍스트로 브리지가 호출되지만, mocked 시장시간이라 environment=test → 실 카운터 0.
    s = _settings()
    _approve_limit(tmp_path, s)
    bridge = RobinhoodMcpBuyExecutor(worker_context=True, submit_fn=lambda **kw: {"broker_order_id": "BRIDGE-X"})
    r = _run(tmp_path, s, execute_real=True, executor=bridge, market_open=True)  # market_open 주입 → mocked/test
    assert r.decision == "REAL_SUBMITTED" and r.broker_order_id == "BRIDGE-X"
    assert r.environment == "test" and r.real_order_placed is False and r.real_orders_placed == 0


def test_idempotency_prevents_duplicate_submit(tmp_path):
    s = _settings()
    _approve_limit(tmp_path, s)
    r1 = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r1.decision == "REAL_SUBMITTED"
    r2 = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r2.decision == "BLOCKED" and any("idempotency" in x for x in r2.block_reasons)


# --- 코드에 실 MCP write 도구명 미포함 ---
def test_no_real_mcp_write_tool_names_in_code():
    # 실제 호출 가능한 MCP 도구 네임스페이스(mcp__robinhood…)가 코드에 전혀 없어야 한다(실 write 미도달).
    # (real_order_executor 주석엔 "place_equity_order를 호출하지 않는다"는 설명 문구가 있으므로 namespace로 검사.)
    import inspect
    for mod in (rex, ae):
        assert "mcp__robinhood" not in inspect.getsource(mod)
    # 호출 형태(접두 namespace 없는 도구명 단독 호출)도 없어야 한다 — approved_execution 한정 엄격 검사.
    assert "place_equity_order" not in inspect.getsource(ae)


def test_no_real_counters_in_receipts_file(tmp_path):
    s = _settings()
    _approve_limit(tmp_path, s)
    _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    raw = (tmp_path / "real_execution_receipts.jsonl").read_text(encoding="utf-8")
    assert '"real_order_placed": true' not in raw and '"real_orders_placed": 1' not in raw
