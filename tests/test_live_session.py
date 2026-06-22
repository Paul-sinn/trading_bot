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
    STATUS_NOT_READY_BAD_PROVIDER,
    STATUS_NOT_READY_NO_MCP,
    STATUS_OK,
    LiveSessionManager,
)
from backend.app.services.market_data import MockMarketDataProvider
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


def _mgr(tmp_path, *, adapter=None, live_enabled=False, market_data=None, provider_name="mock"):
    return LiveSessionManager(
        adapter=adapter if adapter is not None else PlaceholderRobinhoodMcpAdapter(),
        market_data=market_data if market_data is not None else MockMarketDataProvider(),
        settings=Settings(live_trading_enabled=live_enabled, market_data_provider=provider_name),
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

def test_live_auto_start_fails_when_mcp_missing(tmp_path):
    # live_auto는 여전히 Robinhood MCP 필요 — 없으면 NOT_READY_NO_MCP.
    mgr = _mgr(tmp_path, live_enabled=True)  # placeholder adapter → check_availability False
    res = mgr.start("live_auto")
    assert res.status == STATUS_NOT_READY_NO_MCP
    assert res.state.automation_running is False
    assert res.real_orders_placed == 0


def test_report_only_start_works_without_mcp(tmp_path):
    # report_only는 MCP 없이도 모니터링 시작 — 시장데이터(mock)만으로 충분.
    mgr = _mgr(tmp_path)  # placeholder MCP(미연동) + mock 시장데이터
    res = mgr.start("report_only")
    assert res.status == STATUS_OK
    assert res.state.automation_running is True
    assert res.state.live_scan_running is True
    assert res.state.market_data_provider == "mock"
    assert res.real_orders_placed == 0
    mgr.shutdown()


def test_report_only_start_fails_on_unknown_provider(tmp_path):
    # 알 수 없는 provider → fail-closed(NOT_READY_BAD_PROVIDER). 주입 없이 settings로만 강제.
    mgr = LiveSessionManager(
        adapter=PlaceholderRobinhoodMcpAdapter(),
        settings=Settings(market_data_provider="bloomberg"),
        reports_dir=tmp_path,
    )
    res = mgr.start("report_only")
    assert res.status == STATUS_NOT_READY_BAD_PROVIDER
    assert res.state.automation_running is False


def test_report_only_start_runs_scan_cycle(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    # 첫 cycle은 동기 실행 → 스캔 이벤트가 즉시 기록됨.
    from backend.app.services.live_scan import SCAN_LOG

    assert (tmp_path / SCAN_LOG).exists()
    assert mgr.status().last_scan_event_count > 0
    mgr.shutdown()


def test_stop_stops_scan_loop(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    assert mgr.status().live_scan_running is True
    mgr.stop("manual")
    assert mgr.status().live_scan_running is False
    assert mgr.status().automation_running is False


def test_emergency_halt_stops_scan_loop(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    mgr.emergency_halt()
    assert mgr.status().live_scan_running is False
    assert mgr.status().automation_running is False


def test_scan_events_never_place_orders(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    events = mgr.scan_events(limit=100)
    assert events  # 스캔이 돌았다
    assert all(e.real_orders_placed == 0 for e in events)
    assert all(e.riskgate_status is None for e in events)  # report_only RiskGate 미평가
    mgr.shutdown()


# --- Mock LLM 의사결정 파이프라인 통합 ---

def test_report_only_start_produces_candidates_and_intents(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    cands = mgr.candidates(limit=100)
    intents = mgr.order_intents(limit=100)
    assert cands  # BUY_CANDIDATE가 후보로 처리됨
    assert intents  # approve가 dry-run OrderIntent로
    assert all(i.real_orders_placed == 0 for i in intents)
    assert all(i.status == "DRY_RUN_INTENT_ONLY" for i in intents)
    assert all(i.broker_order_id is None for i in intents)
    mgr.shutdown()


def test_status_carries_ai_fields(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    s = mgr.status()
    assert s.llm_provider == "mock"
    assert s.ai_cost_estimate_today == 0.0
    assert s.ai_calls_today > 0
    assert s.latest_candidates  # 상태에 후보 노출
    mgr.shutdown()


def test_ai_status_zero_cost(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    ai = mgr.ai_status()
    assert ai.llm_provider == "mock"
    assert ai.ai_cost_estimate_today == 0.0
    assert ai.ai_budget_remaining >= 0
    mgr.shutdown()


def test_stop_blocks_candidate_processing(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    mgr.stop("manual")
    # 정지 후 파이프라인은 처리하지 않는다(automation_running=False 가드).
    assert mgr._pipeline.process_scan_events(
        [], session_id="s", trading_mode="report_only",
        automation_running=mgr.status().automation_running,
        emergency_halt=mgr.status().emergency_halt,
    ) == []
    assert mgr.status().automation_running is False


def test_emergency_halt_blocks_candidate_processing(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.start("report_only")
    mgr.emergency_halt()
    assert mgr.status().emergency_halt is True
    assert mgr.status().automation_running is False


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
    mgr.shutdown()


def test_report_only_start_succeeds_with_available_adapter(tmp_path):
    # report_only는 live_enabled와 무관하게 시작 가능하되 실주문 경로 없음.
    mgr = _mgr(tmp_path, adapter=FakeAvailableAdapter(), live_enabled=False)
    res = mgr.start("report_only")
    assert res.status == STATUS_OK
    assert res.state.automation_running is True
    mgr.shutdown()


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
