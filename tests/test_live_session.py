"""LiveSessionManager + Robinhood MCP placeholder + 라이브 기록 테스트 (spec: specs/live_session.md).

CRITICAL 검증: 실주문 없음(real_orders_placed=0), MCP 없으면 NOT_READY_NO_MCP(크래시 없음),
stop/emergency-halt가 즉시 신규 주문 차단, 일간 기록 저장/주간 집계, placeholder 어댑터는
모든 브로커 호출에서 예외. Shadow 산출물과 분리(live_*.jsonl 전용).
"""

from __future__ import annotations

import pytest

from backend.app.core.config import Settings
from backend.app.services import live_records
from backend.app.services.live_session import (
    STATUS_BLOCKED_EMERGENCY_HALT,
    STATUS_BLOCKED_LIVE_DISABLED,
    STATUS_NOT_READY_NO_MCP,
    STATUS_OK,
    LiveSessionManager,
)
from backend.app.services.robinhood_mcp import (
    PlaceholderRobinhoodMcpAdapter,
    RobinhoodMcpNotConfigured,
)


class FakeAvailableAdapter:
    """연동된 것처럼 동작하는 테스트 더블. 주문 메서드 호출 횟수를 센다(실주문 검증용)."""

    def __init__(self) -> None:
        self.order_calls = 0
        self.cancel_calls = 0

    def check_availability(self) -> bool:
        return True

    def connect(self) -> None:
        return None

    def get_account_status(self) -> dict:
        return {"status": "active"}

    def get_buying_power(self) -> float:
        return 1000.0

    def get_positions(self) -> list[dict]:
        return []

    def get_open_orders(self) -> list[dict]:
        return []

    def cancel_open_orders(self) -> int:
        self.cancel_calls += 1
        return 0

    def place_limit_buy(self, symbol: str, quantity: float, limit_price: float) -> dict:
        self.order_calls += 1
        return {"order_id": "x"}

    def get_order_status(self, order_id: str) -> dict:
        return {"state": "filled"}


def _mgr(tmp_path, *, adapter=None, live_enabled=False):
    return LiveSessionManager(
        adapter=adapter if adapter is not None else PlaceholderRobinhoodMcpAdapter(),
        settings=Settings(live_trading_enabled=live_enabled),
        reports_dir=tmp_path,
    )


# --- placeholder 어댑터: 성공 위조 금지 ---

def test_placeholder_adapter_unavailable():
    assert PlaceholderRobinhoodMcpAdapter().check_availability() is False


@pytest.mark.parametrize(
    "method, args",
    [
        ("connect", ()),
        ("get_account_status", ()),
        ("get_buying_power", ()),
        ("get_positions", ()),
        ("get_open_orders", ()),
        ("cancel_open_orders", ()),
        ("place_limit_buy", ("AAPL", 1.0, 10.0)),
        ("get_order_status", ("oid",)),
    ],
)
def test_placeholder_adapter_raises_on_broker_calls(method, args):
    adapter = PlaceholderRobinhoodMcpAdapter()
    with pytest.raises(RobinhoodMcpNotConfigured):
        getattr(adapter, method)(*args)


# --- start preflight ---

def test_start_fails_when_mcp_missing(tmp_path):
    mgr = _mgr(tmp_path)  # placeholder → check_availability False
    res = mgr.start("report_only")
    assert res.status == STATUS_NOT_READY_NO_MCP
    assert res.state.automation_running is False
    assert res.real_orders_placed == 0


def test_start_blocked_when_live_disabled(tmp_path):
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=False)
    res = mgr.start("live_auto")
    assert res.status == STATUS_BLOCKED_LIVE_DISABLED
    assert res.state.automation_running is False


def test_start_blocked_when_emergency_halt(tmp_path):
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=True)
    mgr.emergency_halt()
    res = mgr.start("live_auto")
    assert res.status == STATUS_BLOCKED_EMERGENCY_HALT
    assert res.state.automation_running is False


def test_successful_mocked_start_sets_running(tmp_path):
    adapter = FakeAvailableAdapter()
    mgr = _mgr(tmp_path, adapter=adapter, live_enabled=True)
    res = mgr.start("live_auto")
    assert res.status == STATUS_OK
    assert res.state.automation_running is True
    assert res.state.session_id is not None
    assert res.state.real_orders_placed == 0
    assert mgr.can_place_new_order() is True
    # 시작은 주문을 내지 않는다(프로브만).
    assert adapter.order_calls == 0


def test_report_only_start_succeeds_with_available_adapter(tmp_path):
    # report_only는 live_enabled와 무관하게 시작 가능하되 실주문 경로 없음.
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=False)
    res = mgr.start("report_only")
    assert res.status == STATUS_OK
    assert res.state.automation_running is True


# --- stop / emergency-halt: 즉시 신규 주문 차단 ---

def test_stop_sets_not_running_and_blocks_orders(tmp_path):
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=True)
    mgr.start("live_auto")
    res = mgr.stop("manual")
    assert res.status == STATUS_OK
    assert res.state.automation_running is False
    assert res.state.stop_reason == "manual"
    assert mgr.can_place_new_order() is False


def test_emergency_halt_blocks_orders(tmp_path):
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=True)
    mgr.start("live_auto")
    res = mgr.emergency_halt()
    assert res.state.emergency_halt is True
    assert res.state.automation_running is False
    assert mgr.can_place_new_order() is False


def test_stop_does_not_place_orders(tmp_path):
    adapter = FakeAvailableAdapter()
    mgr = _mgr(tmp_path, adapter=adapter, live_enabled=True)
    mgr.start("live_auto")
    mgr.stop("manual")
    # 미체결 취소는 시도하되(cancel) 신규 주문은 절대 없음.
    assert adapter.order_calls == 0


def test_status_is_read_only_does_not_start(tmp_path):
    mgr = _mgr(tmp_path)
    before = mgr.status()
    after = mgr.status()
    assert before.automation_running is False
    assert after.automation_running is False  # 새로고침이 매매를 시작하지 않음


# --- 일간/주간 기록 ---

def test_daily_record_written_after_stop(tmp_path):
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=True)
    mgr.start("live_auto")
    mgr.stop("manual")
    records = live_records.load_daily_records(reports_dir=tmp_path)
    assert len(records) >= 1
    assert records[-1].orders_submitted == 0
    assert records[-1].real_orders_placed == 0


def test_weekly_aggregates_daily_records():
    daily = [
        live_records.LiveDailyRecord(date="2026-06-15", orders_submitted=2, orders_filled=1, realized_pnl=-5.0),
        live_records.LiveDailyRecord(date="2026-06-16", orders_submitted=3, orders_filled=2, realized_pnl=10.0),
        live_records.LiveDailyRecord(date="2026-06-22", orders_submitted=1, orders_filled=0, realized_pnl=0.0),
    ]
    weeks = live_records.aggregate_weekly(daily)
    assert len(weeks) == 2  # 6/15·6/16 같은 주(월 6/15), 6/22 다음 주
    w0 = weeks[0]
    assert w0.week_start == "2026-06-15"
    assert w0.trading_days == 2
    assert w0.total_orders == 5
    assert w0.filled_orders == 3
    assert w0.realized_pnl == 5.0
    assert w0.max_daily_loss == -5.0


def test_daily_record_upsert_is_date_idempotent(tmp_path):
    live_records.upsert_daily_record(
        live_records.LiveDailyRecord(date="2026-06-21", session_ids=["a"]), reports_dir=tmp_path
    )
    live_records.upsert_daily_record(
        live_records.LiveDailyRecord(date="2026-06-21", session_ids=["b"]), reports_dir=tmp_path
    )
    records = live_records.load_daily_records(reports_dir=tmp_path)
    same_day = [r for r in records if r.date == "2026-06-21"]
    assert len(same_day) == 1  # 멱등 — 같은 date 한 행만
    assert set(same_day[0].session_ids) == {"a", "b"}


def test_live_records_separate_from_shadow(tmp_path):
    # live 기록은 live_*.jsonl 전용 — shadow 파일명을 건드리지 않는다.
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=True)
    mgr.start("live_auto")
    mgr.stop("manual")
    written = {p.name for p in tmp_path.iterdir()}
    assert "live_sessions.jsonl" in written
    assert "live_daily_records.jsonl" in written
    for shadow_name in ("signal_decision_log.jsonl", "decision_outcome_score.jsonl",
                        "daily_shadow_report.md", "shadow_health_check.json"):
        assert shadow_name not in written
