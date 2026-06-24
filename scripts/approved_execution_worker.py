"""Discord 승인 실행 워커 — 승인된 요청을 받아 게이트 재확인 후 1건만 실행 준비/실행.

모드:
    --dry-run     (기본) 절대 제출하지 않고 READY/BLOCKED 영수증만 기록.
    --execute-real          모든 게이트 통과 시에만 1건 제출(미래 라이브 전용).

CRITICAL: 이 워커는 승인이 리스크 게이트를 우회하지 못하게 모든 게이트를 재확인한다.
실 executor는 항상 disabled(fail-closed)라 --execute-real도 프로덕션에선 BLOCKED가 된다.
실 결선은 검증·greenlight 이후 별도 변경에서만. 시크릿 미출력. Robinhood write 미호출.

실행:
    source .venv/bin/activate
    PYTHONPATH=. python scripts/approved_execution_worker.py --dry-run

spec: specs/real_order_v1_checklist.md §13
"""

from __future__ import annotations

import argparse
import sys

from backend.app.core.config import Settings
from backend.app.services.approved_execution import process_approved_execution


def _print(rcpt) -> None:
    # 시크릿/전체 계좌번호 미출력 — 결정/사유/식별자만.
    print(
        f"[approved-exec] decision={rcpt.decision} symbol={rcpt.symbol} side={rcpt.side} "
        f"order_type={rcpt.order_type} notional={rcpt.notional} approval_id={rcpt.approval_id} "
        f"env={rcpt.environment}/{rcpt.market_hours_source} broker_order_id={rcpt.broker_order_id} "
        f"real_order_placed={rcpt.real_order_placed} real_orders_placed={rcpt.real_orders_placed} "
        f"reason={rcpt.reason}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discord-approved execution worker (dry-run by default — no orders).")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="평가만, 제출 없음(기본)")
    g.add_argument("--execute-real", action="store_true", help="모든 게이트 통과 시 1건 제출(미래 라이브 전용)")
    args = parser.parse_args(argv)

    execute_real = bool(args.execute_real)
    if execute_real:
        print("[approved-exec] --execute-real 요청됨. 실 executor는 fail-closed(disabled)라 프로덕션에선 제출되지 않습니다.")
    rcpt = process_approved_execution(settings=Settings(), execute_real=execute_real)
    _print(rcpt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
