"""v1 일봉 백테스트 실행 CLI (수동 — 네트워크 필요, CI 아님).

헌장 docs/STRATEGY.md §10: 무료 일봉으로 v1 백테스트를 돌려 매매일지·SPY 비교·게이트 체크리스트를
출력한다. ⚠️ go/no-go 최종 판정은 사람이 한다 — 이 스크립트는 판단 근거(숫자)만 출력하고 멈춘다.
실거래·실주문·자동 라이브 진입 없음.

사용:
    .venv/bin/python scripts/run_v1.py --universe AAPL MSFT NVDA SMH XLE --start 2015-01-01 --end 2024-12-31

집계/리포트 로직은 agents/v1_run.py(테스트됨). 이 파일은 얇은 CLI 래퍼다.
"""

from __future__ import annotations

import argparse

from agents.data_adapter import FreeDailyProvider
from agents.v1_run import format_report, run_v1


def main() -> None:
    parser = argparse.ArgumentParser(description="v1 일봉 백테스트 실행 (수동, 네트워크)")
    parser.add_argument("--universe", nargs="+", required=True, help="종목 심볼들(예: AAPL MSFT SMH)")
    parser.add_argument("--spy-symbol", default="SPY")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    # FreeDailyProvider는 yfinance를 지연 import한다(미설치 시 명확한 안내).
    provider = FreeDailyProvider()
    report = run_v1(
        provider,
        args.universe,
        args.start,
        args.end,
        spy_symbol=args.spy_symbol,
    )
    print(format_report(report))


if __name__ == "__main__":
    main()
