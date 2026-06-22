#!/usr/bin/env python3
"""OrderIntent 워커 (dry-run 영수증 전용) — Claude/Codex MCP 워커 entrypoint.

`reports/live_order_intents.jsonl`의 dry-run OrderIntent를 읽어, control_flags + 최신 broker
snapshot으로 재검증한 뒤 `reports/live_order_receipts.jsonl`에 **영수증만** append한다.

CRITICAL 안전:
  - Robinhood write/order/cancel/review 도구를 절대 호출하지 않는다(이 스크립트는 MCP를 안 부른다).
  - 브로커 상태를 바꾸지 않는다. real_orders_placed=0, broker_order_id=None, real_order_placed=False.
  - idempotency_key 멱등: 이미 영수증이 있으면 다시 쓰지 않는다.
  - control_flags/스냅샷 부재 시 fail-closed(BLOCKED).

사용:
  PYTHONPATH=. python -m scripts.order_intent_worker
  PYTHONPATH=. python -m scripts.order_intent_worker --source codex_worker
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.app.services.order_receipt import process_pending_intents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OrderIntent 워커(dry-run 영수증 전용).")
    parser.add_argument(
        "--source",
        choices=["claude_code_worker", "codex_worker"],
        default="claude_code_worker",
        help="영수증 source 라벨",
    )
    args = parser.parse_args(argv)

    written = process_pending_intents(source=args.source)
    summary = {
        "written": len(written),
        "would_submit": sum(1 for r in written if r.decision == "WOULD_SUBMIT"),
        "blocked": sum(1 for r in written if r.decision == "BLOCKED"),
        "errors": sum(1 for r in written if r.decision == "ERROR"),
        "real_orders_placed": 0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in written:
        print(
            f"  {r.symbol} {r.side} ${r.notional} -> {r.decision} ({r.reason})"
            f" | broker_order_id={r.broker_order_id} real_order_placed={r.real_order_placed}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
