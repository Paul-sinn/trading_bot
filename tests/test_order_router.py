"""자동 주문 라우터 v1 테스트 — 후보 선택 + 프리뷰 + 승인 요청(주문 제출 없음).

검증: 전략 intent만 선택 · 테스트성 차단 · 일일 캡 · stale 스냅샷/호가 · 와이드 스프레드 차단 ·
저가주 지정가/고가주 분수 시장가 정책 · 분수 비활성 차단 · 승인 요청 생성 · preview_hash 변경 ·
Discord 승인 필수 · Robinhood write 미사용 · 실주문 0.

spec: specs/real_order_v1_checklist.md §11
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.approval_store import get_request
from backend.app.services.real_order_executor import RealExecutionReceipt, append_execution_receipt
import backend.app.services.order_router as orr
from backend.app.services.order_router import select_and_route
from backend.app.main import app

NOW = datetime(2026, 6, 23, 15, 0, 0, tzinfo=timezone.utc)  # 평일 장중
LIVE = Settings().live_strategy_id


def _intent(symbol="HOOD", strategy_id=LIVE, conf=0.9, side="BUY", decision="approve",
            gate="accepted_dry_run", key=None, notional=50.0, limit=14.0) -> OrderIntent:
    generated_at = NOW.isoformat()
    trading_date = NOW.date().isoformat()
    return OrderIntent(
        timestamp=generated_at, scan_run_id="s1", intent_generated_at=generated_at,
        trading_date=trading_date, session_id="s1", trading_mode="report_only",
        strategy_id=strategy_id, symbol=symbol, side=side, scan_event_key=key or f"{strategy_id}|{symbol}|2026-06-23",
        mock_llm_decision=decision, mock_llm_confidence=conf, mock_llm_reason="ok",
        execution_gate_status=gate, planned_order_type="limit",
        planned_limit_price=limit, planned_notional_usd=notional, planned_quantity=(notional / limit),
    )


def _snap(quotes, *, ts=NOW, account_last4="••••9372", open_orders=None) -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=ts.isoformat(), account_last4=account_last4, buying_power=985.97,
                          positions=[], open_orders=open_orders or [], quotes=quotes)


def _q(symbol, *, bid, ask, price=None, as_of=NOW):
    return {"symbol": symbol, "price": price if price is not None else ask, "bid": bid, "ask": ask,
            "as_of": as_of.isoformat() if as_of else None}


def _settings(**kw) -> Settings:
    base = dict(order_router_max_notional_usd=100.0, order_router_allow_fractional_market_buy=True,
                order_router_max_spread_pct=0.003, order_router_quote_max_age_seconds=30,
                order_router_daily_max_approval_requests=1, max_real_orders_per_day=1,
                require_market_hours_for_real_order=True, require_fresh_broker_snapshot_for_real_order=True,
                market_data_provider="mock")  # hermetic: 라우터는 주입된 스냅샷 호가만(Alpaca 네트워크 배제)
    base.update(kw)
    return Settings(**base)


def _route(intents, snap, settings=None, *, reports_dir, market_open=True):
    return select_and_route(settings=settings or _settings(), reports_dir=reports_dir, now=NOW,
                            market_open=market_open, intents=intents, snapshot=snap, send=False)


# --- 선택 규칙 ---
def test_selects_only_strategy_intent(tmp_path):
    intents = [_intent(symbol="AAA", strategy_id="manual-test", key="m|AAA"),
               _intent(symbol="HOOD", strategy_id=LIVE, key="s|HOOD")]
    r = _route(intents, _snap([_q("AAA", bid=10, ask=10.01), _q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    assert r.selected.symbol == "HOOD" and r.selected.strategy_id == LIVE


def test_test_only_intent_rejected(tmp_path):
    r = _route([_intent(strategy_id="manual-test", key="m|HOOD")],
               _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED" and "자격" in r.reason


def test_non_buy_rejected(tmp_path):
    r = _route([_intent(side="SELL", key="s|HOOD|sell")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"


def test_unapproved_review_rejected(tmp_path):
    r = _route([_intent(decision="veto", key="s|HOOD|veto")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"


# --- 글로벌 차단 ---
def test_daily_real_cap_blocks(tmp_path):
    append_execution_receipt(
        RealExecutionReceipt(intent_id="x", idempotency_key="x", symbol="HOOD", side="BUY", decision="REAL_SUBMITTED",
                             limit_price=14.0, notional=14.0, quantity=1.0, environment="production",
                             market_hours_source="real", is_proof_run=False, broker_order_id="RH-1",
                             real_order_placed=True, real_orders_placed=1, timestamp=NOW.isoformat()),
        reports_dir=tmp_path)
    r = _route([_intent(key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED" and any("MAX_REAL_ORDERS_PER_DAY" in x for x in r.block_reasons)


def test_daily_approval_request_cap_blocks(tmp_path):
    # 첫 라우팅 성공 → 요청 1건. 두번째는 일일 승인요청 캡(1)에 막힘.
    s = _settings()
    r1 = _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), s, reports_dir=tmp_path)
    assert r1.decision == "ROUTER_SELECTED"
    r2 = _route([_intent(symbol="MSFT", key="s|MSFT")], _snap([_q("MSFT", bid=20.0, ask=20.01)]), s, reports_dir=tmp_path)
    assert r2.decision == "ROUTER_BLOCKED" and any("DAILY_MAX_APPROVAL" in x for x in r2.block_reasons)


def test_stale_snapshot_blocks(tmp_path):
    stale = _snap([_q("HOOD", bid=14.0, ask=14.01)], ts=NOW - timedelta(seconds=7200))
    r = _route([_intent(key="s|HOOD")], stale, reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED" and any("stale" in x for x in r.block_reasons)


def test_market_closed_blocks(tmp_path):
    r = _route([_intent(key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path, market_open=False)
    assert r.decision == "ROUTER_BLOCKED" and any("장시간" in x for x in r.block_reasons)


# --- 호가 차단 ---
def test_stale_quote_blocks(tmp_path):
    snap = _snap([_q("HOOD", bid=14.0, ask=14.01, as_of=NOW - timedelta(seconds=600))])
    r = _route([_intent(key="s|HOOD")], snap, reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"  # 호가 stale → 자격 후보 없음


def test_missing_quote_blocks(tmp_path):
    r = _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("ZZZ", bid=10, ask=10.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"


def test_wide_spread_blocks(tmp_path):
    snap = _snap([_q("HOOD", bid=14.0, ask=14.50)])  # 스프레드 ~3.5% >> 0.3%
    r = _route([_intent(key="s|HOOD")], snap, reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"


# --- 주문유형 정책 ---
def test_cheap_stock_limit_preview(tmp_path):
    r = _route([_intent(symbol="HOOD", limit=14.0, key="s|HOOD")],
               _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    p = r.selected
    assert p.order_type == "limit" and p.limit_price is not None and p.quantity >= 1
    assert p.notional <= 100.0 and p.dollar_amount is None
    assert abs(p.quantity * p.limit_price - p.notional) < 0.01


def test_expensive_stock_fractional_market_preview(tmp_path):
    snap = _snap([_q("NVDA", bid=150.0, ask=150.05)])
    r = _route([_intent(symbol="NVDA", conf=0.9, key="s|NVDA")], snap, reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    p = r.selected
    assert p.order_type == "market" and p.dollar_amount == 100.0 and p.quantity is None and p.notional == 100.0


def test_fractional_blocked_when_disabled(tmp_path):
    s = _settings(order_router_allow_fractional_market_buy=False)
    snap = _snap([_q("NVDA", bid=150.0, ask=150.05)])
    r = _route([_intent(symbol="NVDA", key="s|NVDA")], snap, s, reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED" and any("분수 시장가 매수 비활성" in x for x in r.block_reasons)


def test_fractional_blocked_low_confidence(tmp_path):
    snap = _snap([_q("NVDA", bid=150.0, ask=150.05)])
    r = _route([_intent(symbol="NVDA", conf=0.5, key="s|NVDA")], snap, reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED" and any("신뢰도" in x for x in r.block_reasons)


# --- 승인 요청 생성 + preview_hash ---
def test_approval_request_created_correctly(tmp_path):
    r = _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED" and r.approval_id
    req = get_request(r.approval_id, reports_dir=tmp_path)
    assert req is not None and req.type == "BUY" and req.side == "BUY"
    assert req.notional is not None and req.notional <= 100.0
    assert req.status == "PENDING" and req.broker_order_id is None
    assert req.preview_hash and len(req.preview_hash) == 64
    assert req.bid == 14.0 and req.ask == 14.01 and req.spread_pct is not None
    assert req.policy_tier == "2" and req.policy_status == "approved"
    assert req.policy_decision == "allowed" and "실전 매수 허용" in (req.policy_reason or "")
    assert req.scan_run_id == "s1"
    assert req.intent_generated_at == NOW.isoformat()
    assert req.trading_date == NOW.date().isoformat()


def test_stale_intent_previous_trading_date_blocked(tmp_path):
    stale = _intent(symbol="HOOD", key="s1|HOOD|2026-06-22|stale").model_copy(
        update={
            "trading_date": "2026-06-22",
            "timestamp": "2026-06-22T19:00:00+00:00",
            "intent_generated_at": "2026-06-22T19:00:00+00:00",
        }
    )
    r = _route([stale], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"
    assert not (tmp_path / "approval_requests.jsonl").exists()


def test_preview_hash_changes_with_preview(tmp_path):
    s = _settings(order_router_daily_max_approval_requests=5)
    r1 = _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), s, reports_dir=tmp_path)
    r2 = _route([_intent(symbol="AAPL", key="s|AAPL")], _snap([_q("AAPL", bid=20.0, ask=20.01)]), s, reports_dir=tmp_path)
    h1 = get_request(r1.approval_id, reports_dir=tmp_path).preview_hash
    h2 = get_request(r2.approval_id, reports_dir=tmp_path).preview_hash
    assert h1 != h2


def test_duplicate_intent_not_re_requested(tmp_path):
    s = _settings(order_router_daily_max_approval_requests=5)
    r1 = _route([_intent(symbol="HOOD", key="dup|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), s, reports_dir=tmp_path)
    assert r1.decision == "ROUTER_SELECTED"
    # 같은 intent 재라우팅 → 이미 승인요청 존재로 자격 제외 → 후보 없음
    r2 = _route([_intent(symbol="HOOD", key="dup|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), s, reports_dir=tmp_path)
    assert r2.decision == "ROUTER_BLOCKED"


# --- Discord 승인 필수 + 실주문 0 ---
def test_router_only_requests_no_submit(tmp_path):
    r = _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    req = get_request(r.approval_id, reports_dir=tmp_path)
    assert req.status == "PENDING"  # 승인 대기 — 제출 안 됨
    assert r.real_orders_placed == 0
    # 실행 영수증 파일 없음(라우터는 주문/실행을 만들지 않음)
    assert not (tmp_path / "real_execution_receipts.jsonl").exists()


# --- 라이브 유니버스 정책 ---
def test_tier0_never_creates_approval_request(tmp_path):
    r = _route([_intent(symbol="SPY", key="s|SPY")], _snap([_q("SPY", bid=600.0, ask=600.1)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"
    assert not (tmp_path / "approval_requests.jsonl").exists()


def test_watch_never_creates_approval_request(tmp_path):
    r = _route([_intent(symbol="ARM", key="s|ARM")], _snap([_q("ARM", bid=80.0, ask=80.1)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"
    assert not (tmp_path / "approval_requests.jsonl").exists()


def test_needs_review_never_creates_approval_request(tmp_path):
    r = _route([_intent(symbol="SMCI", key="s|SMCI")], _snap([_q("SMCI", bid=40.0, ask=40.05)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"
    assert not (tmp_path / "approval_requests.jsonl").exists()


def test_unknown_ticker_blocked(tmp_path):
    r = _route([_intent(symbol="F", key="s|F")], _snap([_q("F", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_BLOCKED"
    assert not (tmp_path / "approval_requests.jsonl").exists()


def test_tier1_approved_can_be_selected(tmp_path):
    r = _route([_intent(symbol="NVDA", conf=0.9, key="s|NVDA")],
               _snap([_q("NVDA", bid=150.0, ask=150.05)]), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    assert r.selected.symbol == "NVDA"
    assert r.selected.policy_tier == "1"


def test_tier2_requires_stronger_confidence(tmp_path):
    low = _route([_intent(symbol="PLTR", conf=0.84, key="s|PLTR|low")],
                 _snap([_q("PLTR", bid=30.0, ask=30.02)]), reports_dir=tmp_path)
    assert low.decision == "ROUTER_BLOCKED"

    high = _route([_intent(symbol="PLTR", conf=0.85, key="s|PLTR|high")],
                  _snap([_q("PLTR", bid=30.0, ask=30.02)]), reports_dir=tmp_path)
    assert high.decision == "ROUTER_SELECTED"
    assert high.selected.policy_tier == "2"


def test_router_prefers_tier1_over_tier2_when_scores_are_close(tmp_path):
    intents = [
        _intent(symbol="PLTR", conf=0.95, key="s|PLTR"),
        _intent(symbol="AAPL", conf=0.86, key="s|AAPL"),
    ]
    snap = _snap([_q("PLTR", bid=30.0, ask=30.02), _q("AAPL", bid=50.0, ask=50.02)])
    r = _route(intents, snap, _settings(order_router_daily_max_approval_requests=5), reports_dir=tmp_path)
    assert r.decision == "ROUTER_SELECTED"
    assert r.selected.symbol == "AAPL"
    assert r.selected.policy_tier == "1"


def test_no_robinhood_write_tool_in_router():
    import inspect
    text = inspect.getsource(orr)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text


def test_router_decision_persisted_real_orders_zero(tmp_path):
    _route([_intent(symbol="HOOD", key="s|HOOD")], _snap([_q("HOOD", bid=14.0, ask=14.01)]), reports_dir=tmp_path)
    raw = (tmp_path / "order_router_decisions.jsonl").read_text(encoding="utf-8")
    assert '"real_orders_placed": 0' in raw


# --- API 읽기 전용 ---
def test_api_router_status_and_latest(tmp_path, monkeypatch):
    import backend.app.services.approval_store as store
    monkeypatch.setattr(orr, "DEFAULT_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(store, "DEFAULT_REPORTS_DIR", tmp_path)
    # API status는 실제 now로 일일 카운트를 집계하므로 라우팅도 실제 now로 정렬한다.
    real_now = datetime.now(timezone.utc)
    intent = _intent(symbol="HOOD", key=f"s|HOOD|{real_now.date().isoformat()}").model_copy(
        update={
            "timestamp": real_now.isoformat(),
            "intent_generated_at": real_now.isoformat(),
            "trading_date": real_now.date().isoformat(),
        }
    )
    select_and_route(settings=_settings(), reports_dir=tmp_path, now=real_now, market_open=True, send=False,
                     intents=[intent],
                     snapshot=_snap([_q("HOOD", bid=14.0, ask=14.01, as_of=real_now)], ts=real_now))
    c = TestClient(app)
    status = c.get("/api/live/order-router/status").json()
    assert status["max_notional_usd"] == 100.0 and status["real_orders_placed"] == 0
    assert status["approval_requests_today"] >= 1
    latest = c.get("/api/live/order-router/latest").json()
    assert latest["decision"] == "ROUTER_SELECTED" and latest["selected"]["symbol"] == "HOOD"
