"""장중 오케스트레이터 러너 — 1회(--once) 또는 루프(--loop).

스냅샷 신선도 확인 → report_only 스캔 → 자동 주문 라우터 → (선택 시) Discord 승인 요청까지만 한다.
**실주문을 절대 내지 않는다.** Robinhood write/order MCP 도구를 import·호출하지 않는다. 시크릿 미출력.

실행:
    source .venv/bin/activate
    PYTHONPATH=. python scripts/run_market_orchestrator.py --once
    PYTHONPATH=. python scripts/run_market_orchestrator.py --loop

spec: specs/real_order_v1_checklist.md §12
"""

from __future__ import annotations

import argparse
import sys
import time

from backend.app.core.config import Settings
from backend.app.services.market_hours_orchestrator import get_orchestrator


def _print_event(ev) -> None:
    # 시크릿/계좌번호 미출력 — 결정/사유/승인 id만.
    print(
        f"[orchestrator] {ev.timestamp} market_open={ev.market_open} action={ev.action} "
        f"result={ev.result} router={ev.router_decision} approval_id={ev.approval_id} "
        f"real_orders_placed={ev.real_orders_placed} reason={ev.reason}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market-hours supervised orchestrator (approval requests only — no orders).")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true", help="1회 실행 후 종료")
    g.add_argument("--loop", action="store_true", help="interval마다 반복(Ctrl+C로 종료)")
    args = parser.parse_args(argv)

    settings = Settings()
    orch = get_orchestrator()

    if args.loop:
        interval = max(30, int(settings.orchestrator_interval_seconds))
        print(f"[orchestrator] loop 시작 — interval={interval}s. 승인 요청만 생성(주문 없음). Ctrl+C로 종료.")
        try:
            while True:
                _print_event(orch.run_once(settings=settings))
                time.sleep(interval)
        except KeyboardInterrupt:
            print("[orchestrator] 종료.")
            return 0

    # 기본: 1회 실행.
    _print_event(orch.run_once(settings=settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
