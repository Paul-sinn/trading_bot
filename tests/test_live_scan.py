"""LiveScanLoop 테스트 (spec: specs/live_scan.md).

베이스라인 유니버스만 스캔, jsonl 기록, real_orders_placed=0, INSUFFICIENT_DATA/ERROR graceful,
드리프트 가드(LIVE_BASELINE_UNIVERSE == experiments.BASELINE_UNIVERSE). 주문/LLM 없음.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.services.live_scan import (
    BUY_CANDIDATE,
    ERROR,
    INSUFFICIENT_DATA,
    LIVE_BASELINE_UNIVERSE,
    SCAN_LOG,
    LiveScanLoop,
    load_scan_events,
)
from backend.app.services.market_data import MockMarketDataProvider

_ALLOWED = {"BUY_CANDIDATE", "REJECT", "SKIP", "INSUFFICIENT_DATA", "ERROR"}


def test_universe_matches_baseline_no_drift():
    # Live 유니버스는 리서치 베이스라인과 정확히 일치해야 한다(기본 유니버스 변경 금지).
    from experiments.universe_bias_test import BASELINE_UNIVERSE

    assert tuple(LIVE_BASELINE_UNIVERSE) == BASELINE_UNIVERSE


def test_scan_cycle_writes_jsonl_and_covers_universe(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")

    assert len(events) == len(LIVE_BASELINE_UNIVERSE)
    assert {e.symbol for e in events} == set(LIVE_BASELINE_UNIVERSE)

    path = tmp_path / SCAN_LOG
    assert path.exists()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == len(events)
    for rec in lines:
        assert rec["real_orders_placed"] == 0
        assert rec["scan_status"] in _ALLOWED
        assert rec["riskgate_status"] is None  # report_only는 RiskGate 미평가


def test_scan_events_have_consistent_buy_candidate(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    for e in events:
        assert e.buy_candidate == (e.scan_status == BUY_CANDIDATE)
        assert e.real_orders_placed == 0


def test_mock_produces_at_least_one_buy_candidate(tmp_path):
    # 합성 데이터가 의미 있는 BUY 경로를 만든다(배관 end-to-end 검증).
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    assert any(e.scan_status == BUY_CANDIDATE for e in events)


def test_insufficient_data_when_bars_short(tmp_path):
    # 200봉 미만 → INSUFFICIENT_DATA(추측 금지).
    short = MockMarketDataProvider(length=50)
    loop = LiveScanLoop(short, reports_dir=tmp_path)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    assert all(e.scan_status == INSUFFICIENT_DATA for e in events)


def test_provider_error_becomes_error_event(tmp_path):
    class BoomProvider:
        name = "boom"

        def get_recent_bars(self, symbol, lookback_days=260):
            raise ConnectionError("network down")

        def get_quote(self, symbol):  # pragma: no cover
            raise ConnectionError("network down")

        def get_quotes(self, symbols):  # pragma: no cover
            return {}

        def provider_status(self):  # pragma: no cover
            from backend.app.services.market_data import ProviderStatus

            return ProviderStatus(name=self.name, available=False)

    loop = LiveScanLoop(BoomProvider(), reports_dir=tmp_path)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    # SPY/VIX 조회부터 실패 → 레짐 None → 전 심볼 INSUFFICIENT_DATA 또는 ERROR(graceful, 크래시 없음).
    assert len(events) == len(LIVE_BASELINE_UNIVERSE)
    assert all(e.scan_status in {INSUFFICIENT_DATA, ERROR} for e in events)


def test_max_symbols_caps_cycle(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path, max_symbols=3)
    events = loop.scan_cycle(session_id="s1", trading_mode="report_only")
    assert len(events) == 3


def test_load_scan_events_tail_readonly(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path)
    loop.scan_cycle(session_id="s1", trading_mode="report_only")
    loop.scan_cycle(session_id="s1", trading_mode="report_only")
    tail = load_scan_events(limit=5, reports_dir=tmp_path)
    assert len(tail) == 5
    assert all(e.real_orders_placed == 0 for e in tail)


def test_scan_path_has_no_llm_or_broker_imports():
    # 정적 가드: 스캔 경로는 LLM·브로커·Robinhood·주문 코드를 참조하지 않는다.
    import backend.app.services.live_scan as scan_mod

    src = Path(scan_mod.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("openai", "claude", "robinhood", "place_limit", "place_order", "mcp"):
        assert forbidden not in src, f"스캔 경로에 금지 참조: {forbidden}"


def test_scan_does_not_write_shadow_files(tmp_path):
    loop = LiveScanLoop(MockMarketDataProvider(), reports_dir=tmp_path)
    loop.scan_cycle(session_id="s1", trading_mode="report_only")
    written = {p.name for p in tmp_path.iterdir()}
    assert SCAN_LOG in written
    for shadow_name in ("signal_decision_log.jsonl", "decision_outcome_score.jsonl",
                        "daily_shadow_report.md", "shadow_health_check.json"):
        assert shadow_name not in written
