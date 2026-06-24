"""Discord 승인 실행 워커 v1 테스트 — 게이트 재확인 + dry-run/실행. 구현/테스트 중 실주문 없음.

검증: rejected/expired/wrong-user/preview_hash/test-only/daily-cap/stale/market-closed/dup-open-buy 차단 ·
dry-run → APPROVED_READY_DRY_RUN · mock executor → REAL_SUBMITTED(test only, 실 카운터 0) ·
Robinhood write 미사용 · 실주문 0.

spec: specs/real_order_v1_checklist.md §13
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from backend.app.services.real_order_executor import (
    MockOrderExecutor,
    RealExecutionReceipt,
    append_execution_receipt,
)
import backend.app.services.approved_execution as ae
from backend.app.services.approved_execution import process_approved_execution

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)  # 평일 장중
LIVE = Settings().live_strategy_id


def _settings(**kw) -> Settings:
    base = dict(discord_allowed_user_ids="U1", enable_real_order_execution=False,
                max_notional_per_real_order_usd=100.0, max_real_orders_per_day=1,
                require_market_hours_for_real_order=True, require_fresh_broker_snapshot_for_real_order=True,
                agentic_account_only=True, strategy_intent_only_for_real_order=True)
    base.update(kw)
    return Settings(**base)


def _snap(account_last4="••••9372", bp=985.97, open_orders=None, ts=NOW) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=ts.isoformat(), account_last4=account_last4, buying_power=bp,
                          positions=[], open_orders=open_orders or [])


def _intent(symbol="F", strategy_id=LIVE, notional=50.0, limit=14.0, key="s|F") -> OrderIntent:
    return OrderIntent(timestamp=NOW.isoformat(), session_id="s1", trading_mode="report_only",
                       strategy_id=strategy_id, symbol=symbol, side="BUY", scan_event_key=key,
                       mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="ok",
                       execution_gate_status="accepted_dry_run", planned_order_type="limit",
                       planned_limit_price=limit, planned_notional_usd=notional, planned_quantity=notional / limit)


def _approved(tmp_path, settings, *, snap=None, decision="APPROVE", user="U1", valid=True, intent=None):
    """fresh snapshot + 전략 intent 승인 요청 + 결정 기록. 반환=요청."""
    snap = snap if snap is not None else _snap()
    append_snapshot(snap, reports_dir=tmp_path)
    req = create_approval_request(intent or _intent(), type="BUY", settings=settings, snapshot=snap,
                                  now=NOW, reports_dir=tmp_path, send=False)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision=decision, discord_user_id=user,
                                     valid=valid, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    return req


def _run(tmp_path, settings, *, now=NOW, market_open=True, execute_real=False, executor=None):
    return process_approved_execution(settings=settings, reports_dir=tmp_path, now=now,
                                      market_open=market_open, execute_real=execute_real, executor=executor)


# --- 차단 매트릭스 ---
def test_no_approval_blocks(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and "승인" in r.reason and r.real_order_placed is False


def test_rejected_approval_blocks(tmp_path):
    _approved(tmp_path, _settings(), decision="REJECT")
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and r.real_orders_placed == 0


def test_expired_approval_blocks(tmp_path):
    s = _settings(approval_request_ttl_seconds=60)
    _approved(tmp_path, s)
    r = _run(tmp_path, s, now=NOW + timedelta(seconds=120))
    assert r.decision == "BLOCKED" and any("EXPIRED" in x for x in r.block_reasons)


def test_wrong_user_blocks(tmp_path):
    _approved(tmp_path, _settings(), user="U9")  # 허용 목록은 U1
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and any("허용 사용자" in x for x in r.block_reasons)


def test_preview_hash_mismatch_blocks(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    intent = _intent()
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="F", side="BUY", order_type="limit", quantity=intent.planned_quantity,
                          limit_price=14.0, notional=50.0, account_last4="••••9372",
                          source_intent_id="s|F", strategy_id=LIVE, idempotency_key="s|F",
                          preview_hash="deadbeef", status="PENDING")  # 잘못된 해시
    append_request(req, reports_dir=tmp_path)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and any("preview_hash" in x for x in r.block_reasons)


def test_test_only_intent_blocks(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    ph = compute_preview_hash(type="BUY", symbol="F", side="BUY", order_type="limit", quantity=1.0,
                              limit_price=14.0, notional=50.0, account_last4="••••9372",
                              source_intent_id="m|F", strategy_id="manual-test", idempotency_key="m|F")
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="F", side="BUY", order_type="limit", quantity=1.0, limit_price=14.0,
                          notional=50.0, account_last4="••••9372", source_intent_id="m|F",
                          strategy_id="manual-test", idempotency_key="m|F", preview_hash=ph, status="PENDING")
    append_request(req, reports_dir=tmp_path)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and any("test-only" in x for x in r.block_reasons)


def test_daily_cap_blocks(tmp_path):
    _approved(tmp_path, _settings())
    append_execution_receipt(
        RealExecutionReceipt(intent_id="x", idempotency_key="x", symbol="F", side="BUY", decision="REAL_SUBMITTED",
                             environment="production", market_hours_source="real", is_proof_run=False,
                             broker_order_id="RH-1", real_order_placed=True, real_orders_placed=1,
                             timestamp=NOW.isoformat()),
        reports_dir=tmp_path)
    r = _run(tmp_path, _settings())
    assert r.decision == "BLOCKED" and any("MAX_REAL_ORDERS_PER_DAY" in x for x in r.block_reasons)


def test_stale_snapshot_blocks(tmp_path):
    s = _settings()
    _approved(tmp_path, s)
    append_snapshot(_snap(ts=NOW - timedelta(seconds=7200)), reports_dir=tmp_path)  # 최신=stale
    r = _run(tmp_path, s)
    assert r.decision == "BLOCKED" and any("stale" in x for x in r.block_reasons)


def test_market_closed_blocks(tmp_path):
    _approved(tmp_path, _settings())
    r = _run(tmp_path, _settings(), market_open=False)
    assert r.decision == "BLOCKED" and any("장시간" in x for x in r.block_reasons)


def test_duplicate_open_buy_blocks(tmp_path):
    s = _settings()
    _approved(tmp_path, s)
    append_snapshot(_snap(open_orders=[{"symbol": "F", "side": "buy", "state": "new"}]), reports_dir=tmp_path)
    r = _run(tmp_path, s)
    assert r.decision == "BLOCKED" and any("중복 미체결 매수" in x for x in r.block_reasons)


# --- 통과 경로 ---
def test_dry_run_produces_approved_ready(tmp_path):
    s = _settings()
    _approved(tmp_path, s)
    r = _run(tmp_path, s, execute_real=False)
    assert r.decision == "APPROVED_READY_DRY_RUN"
    assert r.broker_order_id is None and r.real_order_placed is False and r.real_orders_placed == 0
    assert r.approval_id and r.order_type == "limit"


def test_mock_executor_real_submitted_test_only(tmp_path):
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)
    r = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "REAL_SUBMITTED"
    assert r.broker_order_id and r.broker_order_id.startswith("MOCK-")
    assert r.environment == "test" and r.is_proof_run is True
    # mock = 실주문 아님 → 실 카운터 0 강제
    assert r.real_order_placed is False and r.real_orders_placed == 0


def test_execute_real_without_executor_fail_closed(tmp_path):
    # 실 executor 기본 → 항상 disabled → BLOCKED(fail-closed). production(real 시장시간)으로 평가.
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)
    r = process_approved_execution(settings=s, reports_dir=tmp_path, now=NOW, execute_real=True)  # market_open=None → real
    assert r.decision == "BLOCKED" and r.environment == "production"
    assert "fail-closed" in r.reason  # 게이트는 통과, 실 executor가 disabled로 차단
    assert r.real_order_placed is False and r.real_orders_placed == 0 and r.broker_order_id is None


# --- 실 BUY 제출 워커 v1: limit/fractional + 멱등 ---
def test_dry_run_never_submits(tmp_path):
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)
    r = _run(tmp_path, s, execute_real=False, executor=MockOrderExecutor())  # dry-run이면 executor 무시
    assert r.decision == "APPROVED_READY_DRY_RUN" and r.broker_order_id is None
    assert r.real_order_placed is False and r.real_orders_placed == 0


def test_execute_real_blocked_without_approval(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)  # 승인 없음
    r = _run(tmp_path, _settings(enable_real_order_execution=True), execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "BLOCKED" and r.real_orders_placed == 0 and r.broker_order_id is None


def test_limit_buy_mock_submit_succeeds(tmp_path):
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)  # 기본 intent: limit
    r = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "REAL_SUBMITTED" and r.order_type == "limit"
    assert r.broker_order_id and r.broker_order_id.startswith("MOCK-")
    assert r.environment == "test" and r.real_order_placed is False and r.real_orders_placed == 0


def test_fractional_market_mock_submit_succeeds(tmp_path):
    s = _settings(enable_real_order_execution=True)
    snap = _snap()
    append_snapshot(snap, reports_dir=tmp_path)
    # 고가주 분수 시장가: order_type=market, dollar_amount<=100, quantity/limit None.
    ph = compute_preview_hash(type="BUY", symbol="NVDA", side="BUY", order_type="market", quantity=None,
                              limit_price=None, notional=100.0, account_last4="••••9372",
                              source_intent_id="s|NVDA", strategy_id=LIVE, idempotency_key="s|NVDA")
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="NVDA", side="BUY", order_type="market", quantity=None,
                          dollar_amount=100.0, limit_price=None, notional=100.0, account_last4="••••9372",
                          source_intent_id="s|NVDA", strategy_id=LIVE, idempotency_key="s|NVDA",
                          preview_hash=ph, status="PENDING")
    append_request(req, reports_dir=tmp_path)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    r = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r.decision == "REAL_SUBMITTED" and r.order_type == "market"
    assert r.broker_order_id and r.broker_order_id.startswith("MOCK-MKT-")
    assert r.dollar_amount == 100.0 and r.real_orders_placed == 0


def test_idempotency_prevents_duplicate_submit(tmp_path):
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)
    r1 = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r1.decision == "REAL_SUBMITTED"
    # 같은 승인/intent 재실행 → 멱등으로 차단(2차 주문 없음).
    r2 = _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    assert r2.decision == "BLOCKED" and any("idempotency" in x for x in r2.block_reasons)


def test_over_cap_blocks(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    s = _settings()
    # notional > $100 인 승인 요청을 직접 생성(라우터/생성기는 막지만 게이트 재확인을 검증).
    ph = compute_preview_hash(type="BUY", symbol="F", side="BUY", order_type="limit", quantity=10.0,
                              limit_price=15.0, notional=150.0, account_last4="••••9372",
                              source_intent_id="big|F", strategy_id=LIVE, idempotency_key="big|F")
    req = ApprovalRequest(created_at=NOW.isoformat(), expires_at=(NOW + timedelta(minutes=10)).isoformat(),
                          type="BUY", symbol="F", side="BUY", order_type="limit", quantity=10.0, limit_price=15.0,
                          notional=150.0, account_last4="••••9372", source_intent_id="big|F",
                          strategy_id=LIVE, idempotency_key="big|F", preview_hash=ph, status="PENDING")
    append_request(req, reports_dir=tmp_path)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1",
                                     valid=True, decided_at=NOW.isoformat()), reports_dir=tmp_path)
    r = _run(tmp_path, s)
    assert r.decision == "BLOCKED" and any("MAX_NOTIONAL_PER_REAL_ORDER" in x for x in r.block_reasons)


# --- 안전 ---
def test_no_robinhood_write_tool_in_module():
    import inspect
    text = inspect.getsource(ae)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text


def test_worker_cli_no_robinhood():
    from pathlib import Path
    text = Path("scripts/approved_execution_worker.py").read_text(encoding="utf-8")
    assert "mcp__robinhood" not in text and "place_equity_order" not in text


def test_no_real_orders_placed_in_any_receipt(tmp_path):
    s = _settings(enable_real_order_execution=True)
    _approved(tmp_path, s)
    _run(tmp_path, s, execute_real=True, executor=MockOrderExecutor())
    raw = (tmp_path / "real_execution_receipts.jsonl").read_text(encoding="utf-8")
    assert '"real_order_placed": true' not in raw
    assert '"real_orders_placed": 1' not in raw
