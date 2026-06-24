from __future__ import annotations

import json
from datetime import datetime, timezone

import scripts.dev_cleanup_approval_state as cleanup


def _write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_dev_cleanup_archives_only_stale_test_approval_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "ARCHIVE_ROOT", tmp_path / "archive")
    now = datetime(2026, 6, 24, 7, 0, tzinfo=timezone.utc)

    _write(
        tmp_path / cleanup.APPROVAL_REQUESTS,
        [
            {
                "approval_id": "old-test",
                "status": "PENDING",
                "type": "BUY",
                "symbol": "AAPL",
                "strategy_id": "test_discord_approval_smoke",
                "created_at": "2026-06-24T02:00:00+00:00",
                "expires_at": "2026-06-24T02:05:00+00:00",
            },
            {
                "approval_id": "live-future",
                "status": "PENDING",
                "type": "BUY",
                "symbol": "MSFT",
                "strategy_id": cleanup.LIVE_STRATEGY_ID,
                "created_at": "2026-06-24T07:00:00+00:00",
                "expires_at": "2026-06-24T07:10:00+00:00",
            },
        ],
    )
    _write(
        tmp_path / cleanup.APPROVAL_DECISIONS,
        [
            {"approval_id": "old-test", "decision": "APPROVE", "valid": True},
            {"approval_id": "live-future", "decision": "APPROVE", "valid": True},
        ],
    )
    _write(
        tmp_path / cleanup.ORDER_ROUTER_DECISIONS,
        [
            {"decision": "ROUTER_BLOCKED", "reason": "장시간 아님", "real_orders_placed": 0},
            {"decision": "ROUTER_SELECTED", "approval_id": "live-future", "real_orders_placed": 0},
        ],
    )
    _write(
        tmp_path / cleanup.REAL_EXECUTION_RECEIPTS,
        [
            {
                "decision": "REAL_SUBMITTED",
                "environment": "production",
                "real_order_placed": True,
                "real_orders_placed": 1,
            }
        ],
    )

    plan = cleanup.build_plan(now=now)
    assert [r.data["approval_id"] for r in plan.archive_by_file[cleanup.APPROVAL_REQUESTS]] == ["old-test"]
    assert [r.data["approval_id"] for r in plan.archive_by_file[cleanup.APPROVAL_DECISIONS]] == ["old-test"]
    assert len(plan.archive_by_file[cleanup.ORDER_ROUTER_DECISIONS]) == 1
    assert len(plan.keep_forever[cleanup.REAL_EXECUTION_RECEIPTS]) == 1

    archive_dir = cleanup.apply_plan(plan)
    assert archive_dir.exists()
    remaining_requests = (tmp_path / cleanup.APPROVAL_REQUESTS).read_text(encoding="utf-8")
    assert "old-test" not in remaining_requests
    assert "live-future" in remaining_requests
    receipts = (tmp_path / cleanup.REAL_EXECUTION_RECEIPTS).read_text(encoding="utf-8")
    assert "REAL_SUBMITTED" in receipts


def test_dev_cleanup_refuses_router_records_with_real_orders(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "ARCHIVE_ROOT", tmp_path / "archive")
    _write(
        tmp_path / cleanup.ORDER_ROUTER_DECISIONS,
        [{"decision": "ROUTER_BLOCKED", "real_orders_placed": 1}],
    )

    plan = cleanup.build_plan(now=datetime(2026, 6, 24, 7, 0, tzinfo=timezone.utc))
    assert plan.errors
    assert plan.archive_by_file[cleanup.ORDER_ROUTER_DECISIONS] == []
