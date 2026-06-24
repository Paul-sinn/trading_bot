#!/usr/bin/env python3
"""Archive stale/dev approval state for next-market supervised dry-runs.

Default mode is --dry-run. With --apply, selected records are moved to an
append-only archive directory under reports/archive/ and removed from the live
state files. This script never touches broker snapshots and never rewrites
production REAL_SUBMITTED execution receipts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"
ARCHIVE_ROOT = REPORTS_DIR / "archive"
LIVE_STRATEGY_ID = "ts_momentum_pullback_v1"

APPROVAL_REQUESTS = "approval_requests.jsonl"
APPROVAL_DECISIONS = "approval_decisions.jsonl"
ORDER_ROUTER_DECISIONS = "order_router_decisions.jsonl"
REAL_EXECUTION_RECEIPTS = "real_execution_receipts.jsonl"
ORCHESTRATOR_EVENTS = "orchestrator_events.jsonl"


@dataclass(frozen=True)
class JsonlRecord:
    line_no: int
    raw: str
    data: dict[str, Any]


@dataclass
class Plan:
    archive_by_file: dict[str, list[JsonlRecord]]
    keep_forever: dict[str, list[JsonlRecord]]
    do_not_touch: dict[str, str]
    archived_request_ids: set[str]
    errors: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_jsonl(filename: str) -> list[JsonlRecord]:
    path = REPORTS_DIR / filename
    if not path.exists():
        return []
    records: list[JsonlRecord] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"_parse_error": True}
        records.append(JsonlRecord(idx, raw, data if isinstance(data, dict) else {"_non_object": True}))
    return records


def _write_jsonl(filename: str, records: list[JsonlRecord]) -> None:
    path = REPORTS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(record.raw + "\n" for record in records)
    path.write_text(text, encoding="utf-8")


def _archive_records(archive_dir: Path, filename: str, records: list[JsonlRecord]) -> None:
    if not records:
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_file": str(Path("reports") / filename),
        "archived_at": _now().isoformat(),
        "records": [
            {"line_no": record.line_no, "data": record.data}
            for record in records
        ],
    }
    (archive_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _request_is_expired(record: JsonlRecord, *, now: datetime) -> bool:
    expires_at = _parse_ts(record.data.get("expires_at"))
    return bool(expires_at and expires_at < now)


def _request_is_test_only(record: JsonlRecord) -> bool:
    strategy_id = record.data.get("strategy_id")
    return bool(strategy_id and strategy_id != LIVE_STRATEGY_ID)


def _request_created_today(record: JsonlRecord, *, now: datetime) -> bool:
    created_at = _parse_ts(record.data.get("created_at"))
    return bool(created_at and created_at.astimezone(timezone.utc).date() == now.date())


def build_plan(*, now: datetime | None = None) -> Plan:
    now = now or _now()
    archive_by_file: dict[str, list[JsonlRecord]] = {
        APPROVAL_REQUESTS: [],
        APPROVAL_DECISIONS: [],
        ORDER_ROUTER_DECISIONS: [],
        ORCHESTRATOR_EVENTS: [],
    }
    keep_forever: dict[str, list[JsonlRecord]] = {REAL_EXECUTION_RECEIPTS: []}
    do_not_touch = {
        "broker_snapshots.jsonl": "broker evidence / fresh snapshot source",
        REAL_EXECUTION_RECEIPTS: "execution history is inspected only; production REAL_SUBMITTED is keep_forever",
    }
    errors: list[str] = []

    requests = _read_jsonl(APPROVAL_REQUESTS)
    decisions = _read_jsonl(APPROVAL_DECISIONS)
    router = _read_jsonl(ORDER_ROUTER_DECISIONS)
    orchestrator = _read_jsonl(ORCHESTRATOR_EVENTS)
    receipts = _read_jsonl(REAL_EXECUTION_RECEIPTS)

    archived_request_ids: set[str] = set()
    for record in requests:
        request_id = str(record.data.get("approval_id") or "")
        if not request_id:
            continue
        if _request_is_expired(record, now=now) or _request_is_test_only(record):
            archive_by_file[APPROVAL_REQUESTS].append(record)
            archived_request_ids.add(request_id)

    for record in decisions:
        approval_id = str(record.data.get("approval_id") or "")
        decision = str(record.data.get("decision") or "").upper()
        valid = record.data.get("valid")
        if approval_id in archived_request_ids or decision == "REJECT" or valid is False:
            archive_by_file[APPROVAL_DECISIONS].append(record)

    for record in router:
        real_orders_placed = int(record.data.get("real_orders_placed") or 0)
        approval_id = str(record.data.get("approval_id") or "")
        decision = str(record.data.get("decision") or "")
        if real_orders_placed != 0:
            errors.append(f"router line {record.line_no}: real_orders_placed != 0, refusing to archive")
            continue
        if decision == "ROUTER_BLOCKED" or approval_id in archived_request_ids:
            archive_by_file[ORDER_ROUTER_DECISIONS].append(record)

    for record in orchestrator:
        real_orders_placed = int(record.data.get("real_orders_placed") or 0)
        approval_id = str(record.data.get("approval_id") or "")
        if real_orders_placed != 0:
            errors.append(f"orchestrator line {record.line_no}: real_orders_placed != 0, refusing to archive")
            continue
        if approval_id and approval_id in archived_request_ids:
            archive_by_file[ORCHESTRATOR_EVENTS].append(record)

    for record in receipts:
        if record.data.get("decision") == "REAL_SUBMITTED" and record.data.get("environment") == "production":
            keep_forever[REAL_EXECUTION_RECEIPTS].append(record)

    return Plan(
        archive_by_file=archive_by_file,
        keep_forever=keep_forever,
        do_not_touch=do_not_touch,
        archived_request_ids=archived_request_ids,
        errors=errors,
    )


def _remaining_after(filename: str, archived: list[JsonlRecord]) -> list[JsonlRecord]:
    archived_lines = {record.line_no for record in archived}
    return [record for record in _read_jsonl(filename) if record.line_no not in archived_lines]


def _requests_created_today(records: list[JsonlRecord], *, now: datetime) -> int:
    return sum(1 for record in records if _request_created_today(record, now=now))


def print_summary(plan: Plan, *, apply: bool, now: datetime | None = None) -> None:
    now = now or _now()
    mode = "apply" if apply else "dry-run"
    print(f"mode={mode}")
    for filename, records in plan.archive_by_file.items():
        print(f"{filename}: would_archive={len(records)}")
    for filename, records in plan.keep_forever.items():
        print(f"{filename}: keep_forever_REAL_SUBMITTED_production={len(records)}")
    for filename, reason in plan.do_not_touch.items():
        print(f"do_not_touch={filename}: {reason}")

    current_requests = _read_jsonl(APPROVAL_REQUESTS)
    remaining_requests = _remaining_after(APPROVAL_REQUESTS, plan.archive_by_file[APPROVAL_REQUESTS])
    print(f"approval_requests_today_before={_requests_created_today(current_requests, now=now)}")
    print(f"approval_requests_today_after={_requests_created_today(remaining_requests, now=now)}")
    print(
        "approval_daily_cap_would_clear="
        + str(_requests_created_today(current_requests, now=now) > 0 and _requests_created_today(remaining_requests, now=now) == 0)
    )
    print("archived_request_count=" + str(len(plan.archived_request_ids)))
    if plan.errors:
        print("errors=" + "; ".join(plan.errors))
    print("safety=no orders, no broker snapshots touched, no real execution receipts modified, archive only")


def apply_plan(plan: Plan) -> Path:
    if plan.errors:
        raise SystemExit("refusing to apply because safety errors were detected")

    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    archive_dir = ARCHIVE_ROOT / f"approval_state_{stamp}"
    for filename, records in plan.archive_by_file.items():
        _archive_records(archive_dir, filename, records)
        if records:
            _write_jsonl(filename, _remaining_after(filename, records))
    return archive_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive stale/dev approval state. Default is --dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be archived (default)")
    parser.add_argument("--apply", action="store_true", help="archive selected records and rewrite live jsonl files")
    args = parser.parse_args()

    apply = bool(args.apply)
    plan = build_plan()
    print_summary(plan, apply=apply)
    if apply:
        archive_dir = apply_plan(plan)
        print("archive_dir=" + str(archive_dir.relative_to(REPO_ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
