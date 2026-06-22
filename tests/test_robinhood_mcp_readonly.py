"""Robinhood MCP 읽기 전용 클라이언트 안전 테스트 (직접 FastAPI MCP PoC).

spec: specs/robinhood_mcp_readonly.md
검증: write 도구 미노출 / 주문 메서드 ReadOnlyModeError / 기본 비활성·fail-closed /
시크릿·계정번호 미로그(마스킹) / real_orders_placed 불변(0). 실 네트워크/인증 없음.
"""

from __future__ import annotations

import json

import pytest

from backend.app.core.config import Settings
from backend.app.services.robinhood_mcp import RobinhoodMcpNotConfigured
from backend.app.services.robinhood_mcp_readonly import (
    BLOCKED_WRITE_TOOLS,
    READ_ONLY_TOOLS,
    ReadOnlyModeError,
    RobinhoodMcpReadOnlyClient,
    mask_account,
)

# place/cancel/review 전 write 메서드는 enabled 여부와 무관하게 항상 차단되어야 한다.
WRITE_METHODS = [
    "place_equity_order",
    "place_option_order",
    "cancel_equity_order",
    "cancel_option_order",
    "review_equity_order",
    "review_option_order",
]


def test_default_is_disabled_and_fail_closed():
    # 기본 비활성 → 가용성 False, 어떤 네트워크/인증도 없음.
    client = RobinhoodMcpReadOnlyClient()
    assert client.check_availability() is False


def test_enabled_without_transport_still_unavailable():
    # enabled=True여도 트랜스포트 없으면 가용성 False(fail-closed).
    client = RobinhoodMcpReadOnlyClient(enabled=True)
    assert client.check_availability() is False


def test_read_methods_raise_when_not_configured():
    client = RobinhoodMcpReadOnlyClient(enabled=True)  # transport 없음
    for call in (
        lambda: client.get_accounts(),
        lambda: client.get_portfolio("516530169"),
        lambda: client.get_positions("516530169"),
        lambda: client.get_open_orders("516530169"),
        lambda: client.get_quotes(["SPY"]),
    ):
        with pytest.raises(RobinhoodMcpNotConfigured):
            call()


def test_list_tools_exposes_only_read_only_tools():
    client = RobinhoodMcpReadOnlyClient()
    tools = client.list_tools()
    assert set(tools) == set(READ_ONLY_TOOLS)
    # write/action 도구는 절대 노출되지 않는다.
    for blocked in BLOCKED_WRITE_TOOLS:
        assert blocked not in tools


def test_no_write_tools_in_read_only_whitelist():
    # 화이트리스트와 블랙리스트는 교집합이 없어야 한다.
    assert set(READ_ONLY_TOOLS).isdisjoint(BLOCKED_WRITE_TOOLS)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_methods_always_raise_readonly(method, enabled):
    # enabled 값과 무관하게 모든 주문/취소/리뷰 메서드는 ReadOnlyModeError.
    client = RobinhoodMcpReadOnlyClient(enabled=enabled)
    with pytest.raises(ReadOnlyModeError):
        getattr(client, method)("AAPL", 1, 100.0)


def test_non_whitelisted_tool_call_blocked():
    # 트랜스포트가 있어도 화이트리스트 밖 도구(특히 write)는 ReadOnlyModeError.
    def transport(tool, args):  # pragma: no cover - 호출되면 안 됨
        raise AssertionError("transport must not be called for blocked tool")

    client = RobinhoodMcpReadOnlyClient(enabled=True, transport=transport)
    with pytest.raises(ReadOnlyModeError):
        client._call_read("place_equity_order", {})


def test_read_call_uses_transport_when_configured():
    calls: list[tuple[str, dict]] = []

    def transport(tool, args):
        calls.append((tool, args))
        return {"ok": True, "tool": tool}

    client = RobinhoodMcpReadOnlyClient(enabled=True, transport=transport)
    assert client.check_availability() is True
    res = client.get_quotes(["SPY", "AAPL"])
    assert res["tool"] == "get_equity_quotes"
    assert calls == [("get_equity_quotes", {"symbols": ["SPY", "AAPL"]})]


def test_mask_account_keeps_only_last4():
    assert mask_account("516530169") == "••••0169"
    assert mask_account(None) == "••••"
    assert mask_account("") == "••••"


def test_snapshot_masks_account_and_forces_zero_orders(tmp_path):
    client = RobinhoodMcpReadOnlyClient(reports_dir=tmp_path)
    written = client.write_snapshot(
        {"account_number": "516530169", "cash": 2104.75, "real_orders_placed": 7}
    )
    # 계정번호 마스킹 + real_orders_placed 강제 0.
    assert written["account_number"] == "••••0169"
    assert written["real_orders_placed"] == 0

    # 파일에도 전체 계정번호가 평문으로 남지 않는다(시크릿/식별자 미로그).
    raw = (tmp_path / "broker_snapshots.jsonl").read_text(encoding="utf-8")
    assert "516530169" not in raw
    assert "••••0169" in raw
    record = json.loads(raw.strip())
    assert record["real_orders_placed"] == 0


def test_latest_snapshot_roundtrip_and_missing_file(tmp_path):
    client = RobinhoodMcpReadOnlyClient(reports_dir=tmp_path)
    assert client.latest_snapshot() is None  # 파일 부재 → None(크래시 없음)
    client.write_snapshot({"cash": 1.0})
    client.write_snapshot({"cash": 2.0})
    latest = client.latest_snapshot()
    assert latest is not None and latest["cash"] == 2.0  # 최근 1건


def test_no_live_auto_enablement_via_client():
    # 이 클라이언트는 live_trading_enabled/live_auto를 건드리지 않는다.
    settings = Settings()
    assert settings.live_trading_enabled is False
    client = RobinhoodMcpReadOnlyClient(enabled=True, settings=settings)
    # 클라이언트 사용 후에도 설정 불변.
    assert settings.live_trading_enabled is False
    assert not hasattr(client, "place_limit_buy")  # 주문 헬퍼 부재
