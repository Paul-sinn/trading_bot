"""Control flags 테스트 — backend ↔ MCP 워커 안전 신호.

spec: specs/live_session.md
검증: read/write 라운드트립, 부재/손상 → None(fail-closed), LiveSessionManager start/stop/halt가
control_flags.json을 갱신한다. 주문 없음 — real_orders_placed=0.
"""

from __future__ import annotations

from backend.app.core.config import Settings
from backend.app.services.control_flags import (
    ControlFlags,
    read_control_flags,
    write_control_flags,
)
from backend.app.services.live_session import LiveSessionManager


def test_write_read_roundtrip(tmp_path):
    write_control_flags(
        ControlFlags(automation_running=True, block_new_orders=False, reason="start"),
        reports_dir=tmp_path,
    )
    flags = read_control_flags(reports_dir=tmp_path)
    assert flags is not None
    assert flags.automation_running is True
    assert flags.block_new_orders is False
    assert flags.reason == "start"
    assert flags.updated_at  # 자동 갱신


def test_missing_file_returns_none(tmp_path):
    assert read_control_flags(reports_dir=tmp_path) is None  # fail-closed로 간주


def test_malformed_file_returns_none(tmp_path):
    (tmp_path / "control_flags.json").write_text("not json", encoding="utf-8")
    assert read_control_flags(reports_dir=tmp_path) is None


def _manager(tmp_path) -> LiveSessionManager:
    # report_only 시작은 MCP 불요(시장데이터 mock). 결정론적.
    return LiveSessionManager(settings=Settings(market_data_provider="mock"), reports_dir=tmp_path)


def test_start_sets_running_and_unblocks(tmp_path):
    mgr = _manager(tmp_path)
    try:
        mgr.start("report_only")
    finally:
        mgr.shutdown()
    flags = read_control_flags(reports_dir=tmp_path)
    assert flags is not None
    assert flags.automation_running is True
    assert flags.emergency_halt is False
    assert flags.block_new_orders is False
    assert flags.block_new_llm_calls is False
    assert flags.reason == "start"


def test_stop_blocks_new_orders(tmp_path):
    mgr = _manager(tmp_path)
    try:
        mgr.start("report_only")
        mgr.stop("manual")
    finally:
        mgr.shutdown()
    flags = read_control_flags(reports_dir=tmp_path)
    assert flags is not None
    assert flags.automation_running is False
    assert flags.block_new_orders is True
    assert flags.block_new_llm_calls is True
    assert "stop" in flags.reason


def test_emergency_halt_blocks_and_sets_halt(tmp_path):
    mgr = _manager(tmp_path)
    try:
        mgr.start("report_only")
        mgr.emergency_halt()
    finally:
        mgr.shutdown()
    flags = read_control_flags(reports_dir=tmp_path)
    assert flags is not None
    assert flags.emergency_halt is True
    assert flags.automation_running is False
    assert flags.block_new_orders is True
    assert flags.reason == "emergency_halt"
