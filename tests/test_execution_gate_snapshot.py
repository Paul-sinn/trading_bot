"""ExecutionGate × Broker 스냅샷 게이트 테스트 (dry-run, 브로커 호출 없음).

spec: specs/live_decision_pipeline.md
검증: buying_power < planned_notional → reject, 같은 심볼 미체결 매수 중복 → reject,
stale 스냅샷 → config에 따라 warn/reject, 스냅샷 없으면 reject 없음(경고만).
모든 경우 real_orders_placed=0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.broker_snapshot import BrokerSnapshot
from backend.app.services.execution_gate import ExecutionCaps, ExecutionGate
from backend.app.services.live_scan import LIVE_BASELINE_UNIVERSE
from backend.app.services.llm_review import ReviewResult

_CAPS = ExecutionCaps(
    max_notional_per_order_usd=1000.0,
    max_daily_order_intents=20,
    max_total_intended_exposure_usd=5000.0,
)


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evaluate(gate, **overrides):
    kwargs = dict(
        symbol="NVDA",
        price=100.0,
        review=ReviewResult(symbol="NVDA", decision="approve", confidence=0.9, reason="ok"),
        source_status="BUY_CANDIDATE",
        scan_event_key="s1|NVDA|2026-06-22|strat",
        session_id="s1",
        trading_mode="report_only",
        strategy_id="strat",
        universe=LIVE_BASELINE_UNIVERSE,
        existing_intent_keys=set(),
        daily_intent_count=0,
        total_intended_exposure_usd=0.0,
        caps=_CAPS,
        automation_running=True,
        emergency_halt=False,
    )
    kwargs.update(overrides)
    return gate.evaluate(**kwargs)


def test_no_snapshot_does_not_reject_but_warns():
    result, intent = _evaluate(ExecutionGate())  # broker_snapshot 기본 None
    assert result.status == "accepted_dry_run"
    assert any("스냅샷 없음" in w for w in result.warnings)
    assert intent.real_orders_placed == 0


def test_rejects_when_buying_power_below_notional():
    snap = BrokerSnapshot(timestamp=_fresh_ts(), buying_power=500.0)  # planned_notional=1000
    result, intent = _evaluate(ExecutionGate(), broker_snapshot=snap)
    assert result.status == "rejected"
    assert any("BUYING_POWER 부족" in r for r in result.rejection_reasons)
    assert intent.real_orders_placed == 0


def test_accepts_when_buying_power_sufficient():
    snap = BrokerSnapshot(timestamp=_fresh_ts(), buying_power=5000.0)
    result, _ = _evaluate(ExecutionGate(), broker_snapshot=snap)
    assert result.status == "accepted_dry_run"


def test_rejects_duplicate_open_buy_same_symbol():
    snap = BrokerSnapshot(
        timestamp=_fresh_ts(),
        buying_power=5000.0,
        open_orders=[{"symbol": "NVDA", "side": "buy", "state": "new", "quantity": 1.0}],
    )
    result, _ = _evaluate(ExecutionGate(), broker_snapshot=snap)
    assert result.status == "rejected"
    assert any("중복 미체결 매수" in r for r in result.rejection_reasons)


def test_open_sell_or_other_symbol_does_not_block():
    snap = BrokerSnapshot(
        timestamp=_fresh_ts(),
        buying_power=5000.0,
        open_orders=[
            {"symbol": "NVDA", "side": "sell", "state": "new", "quantity": 1.0},
            {"symbol": "AAPL", "side": "buy", "state": "new", "quantity": 1.0},
        ],
    )
    result, _ = _evaluate(ExecutionGate(), broker_snapshot=snap)
    assert result.status == "accepted_dry_run"  # 매도/타심볼은 차단 아님


def test_stale_snapshot_warns_by_default():
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    snap = BrokerSnapshot(timestamp=old, buying_power=5000.0)
    result, _ = _evaluate(ExecutionGate(), broker_snapshot=snap, snapshot_max_age_seconds=3600)
    assert result.status == "accepted_dry_run"
    assert any("stale" in w for w in result.warnings)


def test_stale_snapshot_rejects_when_configured():
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    snap = BrokerSnapshot(timestamp=old, buying_power=5000.0)
    result, _ = _evaluate(
        ExecutionGate(),
        broker_snapshot=snap,
        snapshot_max_age_seconds=3600,
        reject_on_stale_snapshot=True,
    )
    assert result.status == "rejected"
    assert any("stale" in r for r in result.rejection_reasons)
