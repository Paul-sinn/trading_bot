"""Discord 승인 게이트웨이 + 감독 거래 하드리밋 테스트.

검증: 승인 요청 생성(BUY/SELL) · 만료 · reject 차단 · approve는 다음 단계만 허용(자동 제출 아님) ·
잘못된 사용자/중복/알 수 없는 id 거부 · preview_hash 불일치/만료 차단 · 테스트성 intent 실주문 불가 ·
전략 intent 필수 · 일일 캡 1 · notional 캡 100 · API 읽기 전용 · Robinhood write 미사용 · 실주문 0.

spec: specs/real_order_v1_checklist.md §10
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_arm import RealOrderArm, write_arm
from backend.app.services.real_order_executor import process_execution
import backend.app.services.approval_store as store
from backend.app.services.approval_store import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequestRefused,
    append_decision,
    append_request,
    compute_preview_hash,
    create_approval_request,
    effective_status,
    get_request_for_intent,
    to_view,
)
from backend.app.services.approval_gate import evaluate_approval_gate
from backend.app.services.discord_approval import parse_command, process_approval_command
from backend.app.main import app

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)  # 평일 장중
LIVE_STRAT = Settings().live_strategy_id


def _intent(symbol="F", notional=50.0, side="BUY", strategy_id=LIVE_STRAT, key="strat|F|2026-06-23", order_type="limit", limit=14.0) -> OrderIntent:
    return OrderIntent(
        timestamp="2026-06-23T14:00:00+00:00", session_id="s1", trading_mode="report_only",
        strategy_id=strategy_id, symbol=symbol, side=side, scan_event_key=key,
        mock_llm_decision="approve", mock_llm_confidence=0.9, mock_llm_reason="ok",
        execution_gate_status="accepted_dry_run", planned_order_type=order_type,
        planned_limit_price=limit, planned_notional_usd=notional, planned_quantity=(notional / limit),
    )


def _settings(**kw) -> Settings:
    base = dict(
        enable_real_order_execution=True, max_notional_per_real_order_usd=100.0, max_real_orders_per_day=1,
        require_discord_approval_for_real_order=True, strategy_intent_only_for_real_order=True,
        require_market_hours_for_real_order=True, require_fresh_broker_snapshot_for_real_order=True,
        agentic_account_only=True, first_order_manual_test_mode=False, discord_allowed_user_ids="U1,U2",
    )
    base.update(kw)
    return Settings(**base)


def _snap(account_last4="••••9372", bp=985.97) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=NOW.isoformat(), account_last4=account_last4, buying_power=bp,
                          positions=[{"symbol": "F", "quantity": 1.0, "shares_available_for_sells": 1.0}], open_orders=[])


def _arm() -> RealOrderArm:
    return RealOrderArm(armed=True, armed_at=NOW.isoformat(), expires_at=(NOW + timedelta(seconds=120)).isoformat(),
                        max_notional=100.0, reason="rehearsal", created_by="test")


def _req(intent, type="BUY", *, ttl=300, account_last4="••••9372", created=NOW) -> ApprovalRequest:
    ph = compute_preview_hash(
        type=type, symbol=intent.symbol, side=intent.side, order_type=intent.planned_order_type,
        quantity=intent.planned_quantity, limit_price=intent.planned_limit_price,
        notional=intent.planned_notional_usd, account_last4=account_last4,
        source_intent_id=intent.scan_event_key, strategy_id=intent.strategy_id, idempotency_key=intent.scan_event_key,
    )
    return ApprovalRequest(
        created_at=created.isoformat(), expires_at=(created + timedelta(seconds=ttl)).isoformat(),
        type=type, symbol=intent.symbol, side=intent.side, order_type=intent.planned_order_type,
        quantity=intent.planned_quantity, dollar_amount=intent.planned_notional_usd, limit_price=intent.planned_limit_price,
        notional=intent.planned_notional_usd, account_last4=account_last4, source_intent_id=intent.scan_event_key,
        strategy_id=intent.strategy_id, idempotency_key=intent.scan_event_key, preview_hash=ph,
    )


# --- 1) 승인 요청 생성 (BUY / SELL) ---
def test_approval_request_created_for_buy(tmp_path):
    req = create_approval_request(_intent(side="BUY"), type="BUY", settings=_settings(), snapshot=_snap(),
                                  now=NOW, reports_dir=tmp_path, send=False)
    assert req.type == "BUY" and req.side == "BUY" and req.status == "PENDING"
    assert req.account_last4 == "••••9372" and req.broker_order_id is None
    assert req.preview_hash and len(req.preview_hash) == 64
    assert get_request_for_intent(req.source_intent_id, reports_dir=tmp_path).approval_id == req.approval_id


def test_approval_request_created_for_sell(tmp_path):
    req = create_approval_request(_intent(side="SELL", key="strat|F|sell"), type="SELL", settings=_settings(),
                                  snapshot=_snap(), now=NOW, reports_dir=tmp_path, send=False)
    assert req.type == "SELL" and req.side == "SELL"
    assert req.quantity is not None and req.limit_price == 14.0


def test_approval_request_sends_discord(tmp_path):
    sent = []
    create_approval_request(_intent(), type="BUY", settings=_settings(discord_webhook_url="https://x"),
                            snapshot=_snap(), now=NOW, reports_dir=tmp_path, send=True,
                            post=lambda url, payload: sent.append(payload) or True)
    assert sent and "embeds" in sent[0]
    text = str(sent[0])
    assert "!approve" in text and "778689372" not in text  # 전체 계좌번호 미노출


# --- 2) 만료 ---
def test_approval_expires():
    req = _req(_intent(), ttl=60)
    assert effective_status(req, [], now=NOW + timedelta(seconds=30)) == "PENDING"
    assert effective_status(req, [], now=NOW + timedelta(seconds=61)) == "EXPIRED"


# --- 3) reject가 실행을 차단 / approve는 다음 단계만 허용 ---
def _gate(intent, settings, req, decisions, **kw):
    ph = req.preview_hash if kw.get("hash") is None else kw["hash"]
    return evaluate_approval_gate(settings=settings, request=req, decisions=decisions, current_preview_hash=ph,
                                  daily_real_count=kw.get("daily", 0), executed_keys=kw.get("keys", set()),
                                  now=kw.get("now", NOW))


def test_reject_blocks_execution():
    intent, s = _intent(), _settings()
    req = _req(intent)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="REJECT", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec])
    assert not g.approved and any("승인 상태 아님: REJECTED" in x for x in g.block_reasons)


def test_approve_allows_gate():
    intent, s = _intent(), _settings()
    req = _req(intent)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec])
    assert g.approved and g.block_reasons == []


def test_no_request_blocks():
    g = evaluate_approval_gate(settings=_settings(), request=None, decisions=[], current_preview_hash="x", now=NOW)
    assert not g.approved and any("승인 요청 없음" in x for x in g.block_reasons)


def test_pending_blocks():
    intent, s = _intent(), _settings()
    req = _req(intent)
    g = _gate(intent, s, req, [])  # 결정 없음 → PENDING
    assert not g.approved and any("PENDING" in x for x in g.block_reasons)


# --- 6/7) 잘못된 사용자 / 중복 / 알 수 없는 id ---
def test_wrong_discord_user_rejected(tmp_path):
    req = _req(_intent()); append_request(req, reports_dir=tmp_path)
    out = process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U9", discord_username="evil",
                                   settings=_settings(), reports_dir=tmp_path, now=NOW)
    assert out["valid"] is False and "권한 없음" in out["reply"]
    decs = store.decisions_for(req.approval_id, reports_dir=tmp_path)
    assert decs and decs[-1].valid is False and decs[-1].reason == "허용되지 않은 Discord 사용자"


def test_duplicate_approval_rejected(tmp_path):
    req = _req(_intent()); append_request(req, reports_dir=tmp_path)
    first = process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U1", settings=_settings(),
                                     reports_dir=tmp_path, now=NOW)
    assert first["valid"] is True and first["decision"] == "APPROVE"
    dup = process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U2", settings=_settings(),
                                   reports_dir=tmp_path, now=NOW)
    assert dup["valid"] is False and "이미 결정" in dup["reply"]


def test_unknown_approval_id_rejected(tmp_path):
    out = process_approval_command(text="!approve nope123", discord_user_id="U1", settings=_settings(),
                                   reports_dir=tmp_path, now=NOW)
    assert out["valid"] is False and "알 수 없는" in out["reply"]


def test_expired_cannot_be_approved(tmp_path):
    req = _req(_intent(), ttl=10); append_request(req, reports_dir=tmp_path)
    out = process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U1", settings=_settings(),
                                   reports_dir=tmp_path, now=NOW + timedelta(seconds=60))
    assert out["valid"] is False and "만료" in out["reply"]


def test_status_command_does_not_write(tmp_path):
    req = _req(_intent()); append_request(req, reports_dir=tmp_path)
    out = process_approval_command(text=f"!status {req.approval_id}", discord_user_id="U9", settings=_settings(),
                                   reports_dir=tmp_path, now=NOW)
    assert out["wrote_decision"] is False and "status=" in out["reply"]


def test_parse_command():
    assert parse_command("!approve abc") == ("!approve", "abc")
    assert parse_command("!reject  xyz ") == ("!reject", "xyz")
    assert parse_command("!status") is None
    assert parse_command("hello") is None


# --- 8/9) preview_hash 불일치 / 만료 차단 ---
def test_preview_hash_mismatch_blocks():
    intent, s = _intent(), _settings()
    req = _req(intent)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec], hash="DIFFERENT")
    assert not g.approved and any("preview_hash 불일치" in x for x in g.block_reasons)


def test_stale_approval_blocks():
    intent, s = _intent(), _settings()
    req = _req(intent, ttl=30)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec], now=NOW + timedelta(seconds=120))
    assert not g.approved and any("EXPIRED" in x for x in g.block_reasons)


# --- 10/11) 테스트성 intent 불가 / 전략 intent 필수 ---
def test_test_only_intent_refused_at_request():
    with pytest.raises(ApprovalRequestRefused):
        create_approval_request(_intent(strategy_id="manual-test"), type="BUY", settings=_settings(),
                                snapshot=_snap(), now=NOW, send=False)


def test_test_only_intent_cannot_become_real_order(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    write_arm(_arm(), reports_dir=tmp_path)
    # 테스트성 intent(strategy_id != live) → 승인 요청도 없음 → process_execution 차단
    r = process_execution(_intent(strategy_id="manual-test", key="manual|F"), settings=_settings(),
                          reports_dir=tmp_path, now=NOW, market_open=True)
    assert r.decision == "REAL_BLOCKED"
    assert any("test-only" in x for x in r.block_reasons)


def test_strategy_intent_allowed_when_approved(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    write_arm(_arm(), reports_dir=tmp_path)
    s = _settings()
    req = create_approval_request(_intent(), type="BUY", settings=s, snapshot=_snap(), now=NOW,
                                  reports_dir=tmp_path, send=False)
    # 승인 전 → 차단
    r0 = process_execution(_intent(), settings=s, reports_dir=tmp_path, now=NOW, market_open=True)
    assert r0.decision == "REAL_BLOCKED" and any("PENDING" in x for x in r0.block_reasons)
    # 허용 사용자 승인
    process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U1", settings=s,
                             reports_dir=tmp_path, now=NOW)
    # 승인 후 → 다음 단계(REAL_READY_DRY_RUN). 실 제출 없음.
    r1 = process_execution(_intent(), settings=s, reports_dir=tmp_path, now=NOW, market_open=True)
    assert r1.decision == "REAL_READY_DRY_RUN"
    assert r1.real_order_placed is False and r1.real_orders_placed == 0
    assert r1.broker_order_id is None


def test_reject_blocks_process_execution(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    write_arm(_arm(), reports_dir=tmp_path)
    s = _settings()
    req = create_approval_request(_intent(), type="BUY", settings=s, snapshot=_snap(), now=NOW,
                                  reports_dir=tmp_path, send=False)
    process_approval_command(text=f"!reject {req.approval_id}", discord_user_id="U1", settings=s,
                             reports_dir=tmp_path, now=NOW)
    r = process_execution(_intent(), settings=s, reports_dir=tmp_path, now=NOW, market_open=True)
    assert r.decision == "REAL_BLOCKED" and any("REJECTED" in x for x in r.block_reasons)


# --- 12/13) 일일 캡 1 / notional 캡 100 ---
def test_daily_cap_enforced_in_gate():
    intent, s = _intent(), _settings()
    req = _req(intent)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec], daily=1)
    assert not g.approved and any("MAX_REAL_ORDERS_PER_DAY" in x for x in g.block_reasons)


def test_notional_cap_100_refused_at_request():
    with pytest.raises(ApprovalRequestRefused):
        create_approval_request(_intent(notional=150.0), type="BUY", settings=_settings(), snapshot=_snap(),
                                now=NOW, send=False)


def test_notional_cap_100_enforced_in_gate():
    intent, s = _intent(notional=150.0), _settings()
    req = _req(intent)  # 캡 무시하고 직접 만든 요청
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec])
    assert not g.approved and any("MAX_NOTIONAL_PER_REAL_ORDER" in x for x in g.block_reasons)


def test_idempotency_consumed_blocks_gate():
    intent, s = _intent(), _settings()
    req = _req(intent)
    dec = ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True)
    g = _gate(intent, s, req, [dec], keys={intent.scan_event_key})
    assert not g.approved and any("idempotency" in x for x in g.block_reasons)


# --- 14) API 읽기 전용 ---
@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_api_approvals_read_only(reports):
    # API to_view는 실제 now를 쓰므로 만료가 실시간 이후가 되도록 충분히 길게 잡는다.
    real_now = datetime.now(timezone.utc)
    req = _req(_intent(), ttl=86400, created=real_now); append_request(req, reports_dir=reports)
    append_decision(ApprovalDecision(approval_id=req.approval_id, decision="APPROVE", discord_user_id="U1", valid=True),
                    reports_dir=reports)
    client = TestClient(app)
    lst = client.get("/api/live/approvals?limit=50").json()
    assert any(a["approval_id"] == req.approval_id for a in lst)
    one = client.get(f"/api/live/approvals/{req.approval_id}").json()
    assert one["approval_id"] == req.approval_id and one["approve_command"] == f"!approve {req.approval_id}"
    assert one["status"] == "APPROVED"
    latest = client.get("/api/live/approvals/latest").json()
    assert latest["approval_id"] == req.approval_id


def test_api_unknown_approval_returns_null(reports):
    assert TestClient(app).get("/api/live/approvals/nope").json() is None


def test_to_view_includes_commands():
    req = _req(_intent())
    v = to_view(req, now=NOW)
    assert v.approve_command == f"!approve {req.approval_id}"
    assert v.reject_command == f"!reject {req.approval_id}"


# --- 16/17) Robinhood write 미사용 / 실주문 0 ---
def test_no_robinhood_write_tool_in_approval_modules():
    import inspect
    import backend.app.services.approval_store as a1
    import backend.app.services.approval_gate as a2
    import backend.app.services.discord_approval as a3
    for mod in (a1, a2, a3):
        text = inspect.getsource(mod)
        assert "mcp__robinhood" not in text
        assert "place_equity_order" not in text


def test_worker_script_no_robinhood():
    # 워커는 Robinhood MCP 도구/주문 API를 import·호출하지 않는다(문서에서 '호출 안 함'을 명시할 뿐).
    from pathlib import Path
    text = Path("scripts/discord_approval_worker.py").read_text(encoding="utf-8")
    assert "mcp__robinhood" not in text and "place_equity_order" not in text
    assert "robinhood_mcp" not in text and "import robin" not in text.lower()


def test_no_real_orders_placed_anywhere(tmp_path):
    append_snapshot(_snap(), reports_dir=tmp_path)
    write_arm(_arm(), reports_dir=tmp_path)
    s = _settings()
    req = create_approval_request(_intent(), type="BUY", settings=s, snapshot=_snap(), now=NOW,
                                  reports_dir=tmp_path, send=False)
    process_approval_command(text=f"!approve {req.approval_id}", discord_user_id="U1", settings=s,
                             reports_dir=tmp_path, now=NOW)
    r = process_execution(_intent(), settings=s, reports_dir=tmp_path, now=NOW, market_open=True)
    raw = (tmp_path / "real_execution_receipts.jsonl").read_text(encoding="utf-8")
    assert '"real_order_placed": false' in raw and '"real_orders_placed": 0' in raw
    assert r.broker_order_id is None
    # 승인 요청 파일에 broker_order_id null
    assert '"broker_order_id": null' in (tmp_path / "approval_requests.jsonl").read_text(encoding="utf-8")
