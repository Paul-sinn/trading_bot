"""Broker 스냅샷 스키마/빌더/저장소/신선도 테스트 (read-only 워커 브리지).

spec: specs/robinhood_mcp_readonly.md
검증: 원본 MCP 응답 → 살균 스냅샷, agentic 계정 선택, 계정번호 마스킹(전체 미저장),
real_orders_placed=0 강제, append/load/latest, 부재/손상/stale 안전 처리.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.broker_snapshot import (
    BrokerSnapshot,
    append_snapshot,
    build_snapshot_from_raw,
    is_stale,
    latest_snapshot,
    load_snapshots,
    select_agentic_account,
)

# 실제 관측된 MCP 응답 형태(audit)를 본뜬 원본.
RAW = {
    "provider": "robinhood-mcp",
    "source": "claude-code-worker",
    "accounts": {
        "data": {
            "accounts": [
                {"account_number": "516530169", "is_default": True, "agentic_allowed": False},
                {"account_number": "778689372", "nickname": "Agentic", "agentic_allowed": True},
            ]
        }
    },
    "portfolio": {
        "data": {
            "total_value": "1000",
            "cash": "1000",
            "buying_power": {"buying_power": "1000.0000", "display_currency": "USD"},
        }
    },
    "positions": {
        "data": {"positions": [{"symbol": "SPCX", "quantity": "2.0", "average_buy_price": "150.5"}]}
    },
    "open_orders": {
        "data": {"orders": [{"symbol": "NVDA", "side": "buy", "state": "new", "quantity": "1.0"}]}
    },
    "quotes": {
        "data": {"results": [{"quote": {"symbol": "SPY", "last_trade_price": "746.57"}}]}
    },
}


def test_select_agentic_account_prefers_agentic_allowed():
    acct = select_agentic_account(RAW["accounts"])
    assert acct is not None
    assert acct["account_number"] == "778689372"  # agentic_allowed=True 우선


def test_build_snapshot_masks_account_and_maps_fields():
    snap = build_snapshot_from_raw(RAW)
    assert snap.account_last4 == "••••9372"  # agentic 계정의 last4
    assert snap.total_value == 1000.0
    assert snap.cash == 1000.0
    assert snap.buying_power == 1000.0
    assert snap.positions == [
        {"symbol": "SPCX", "quantity": 2.0, "average_buy_price": 150.5, "shares_available_for_sells": None}
    ]
    assert snap.open_orders == [
        {"symbol": "NVDA", "side": "buy", "state": "new", "quantity": 1.0}
    ]
    # 라우터용 호가 필드(bid/ask/as_of)는 원본에 없으면 None.
    assert snap.quotes == [{"symbol": "SPY", "price": 746.57, "bid": None, "ask": None, "as_of": None}]
    assert snap.real_orders_placed == 0
    assert snap.errors == []


def test_full_account_number_never_in_snapshot():
    snap = build_snapshot_from_raw(RAW)
    dumped = snap.model_dump_json()
    assert "778689372" not in dumped  # 전체 계정번호 미저장
    assert "516530169" not in dumped


def test_real_orders_placed_forced_zero_even_if_injected():
    snap = BrokerSnapshot(real_orders_placed=99)
    assert snap.real_orders_placed == 0


def test_build_collects_errors_on_missing_data():
    snap = build_snapshot_from_raw({"accounts": {"data": {"accounts": []}}})
    assert snap.account_last4 == "••••"
    assert any("account" in e for e in snap.errors)
    assert any("portfolio" in e for e in snap.errors)
    assert snap.real_orders_placed == 0


def test_append_load_latest_roundtrip(tmp_path):
    assert latest_snapshot(reports_dir=tmp_path) is None  # 부재 → None
    append_snapshot(BrokerSnapshot(cash=1.0), reports_dir=tmp_path)
    append_snapshot(BrokerSnapshot(cash=2.0), reports_dir=tmp_path)
    snaps = load_snapshots(reports_dir=tmp_path)
    assert [s.cash for s in snaps] == [1.0, 2.0]
    assert latest_snapshot(reports_dir=tmp_path).cash == 2.0  # 최신


def test_load_skips_malformed_lines(tmp_path):
    path = tmp_path / "broker_snapshots.jsonl"
    path.write_text('{"cash": 1.0}\nnot json\n{"cash": 2.0}\n', encoding="utf-8")
    snaps = load_snapshots(reports_dir=tmp_path)
    assert [s.cash for s in snaps] == [1.0, 2.0]  # 손상 라인 skip


def test_append_forces_zero_orders_in_file(tmp_path):
    append_snapshot(BrokerSnapshot(cash=5.0), reports_dir=tmp_path)
    raw = (tmp_path / "broker_snapshots.jsonl").read_text(encoding="utf-8")
    assert '"real_orders_placed": 0' in raw


def test_is_stale_true_for_old_and_unparseable():
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    fresh = BrokerSnapshot(timestamp=(now - timedelta(seconds=10)).isoformat())
    old = BrokerSnapshot(timestamp=(now - timedelta(seconds=7200)).isoformat())
    assert is_stale(fresh, max_age_seconds=3600, now=now) is False
    assert is_stale(old, max_age_seconds=3600, now=now) is True
    # 파싱 불가 timestamp → fail-closed(stale)
    assert is_stale(BrokerSnapshot(timestamp="not-a-date"), max_age_seconds=3600, now=now) is True
