"""생존편향 없는 OOS 재검증 CLI (수동 — 실 데이터 필요, CI 아님).

헌장 §3/§10②: 생존편향 없는 벤더 데이터(상폐종목+시점별 지수편입)로 약세장 포함 OOS 재검증.
두 가지 데이터 소스(둘 다 상폐 포함 = 생존편향 없음):

  1) CSV 드롭인 (벤더 무관):
       <root>/metrics.csv         — symbol,listed_from,delisted_at,avg_dollar_volume,atr_pct,is_leveraged_or_inverse
       <root>/ohlcv/<SYMBOL>.csv  — date,open,high,low,close,volume (상폐종목 포함)
       <root>/vix.csv             — date,close
     사용: python scripts/run_oos.py --source csv --root data/survivorship_free

  2) Norgate SDK 실연동 (윈도우 NDU 필요):
     사용: python scripts/run_oos.py --source norgate --watchlist "S&P 500 Current & Past" --extra SMH XLE XLF

⚠️ 무료체험은 히스토리 2년 제한 → 약세장(2018/2022) 풀 재검증은 정식 구독 후. 트라이얼 스모크는
   --window 로 최근 구간을 직접 지정한다 (예: --window trial=2025-01-01:2025-12-31).

집계·러너 로직은 agents/oos_validate.py(테스트됨). 이 파일은 얇은 CLI다. go/no-go는 사람.
"""

from __future__ import annotations

import argparse
import sys

# 윈도우 콘솔(cp949)에서 리포트의 한글·이모지(❗ 등) 출력이 깨지지 않도록 stdout을 UTF-8로.
try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from agents.data_adapter import CsvPointInTimeProvider, NorgateProvider
from agents.oos_validate import format_oos_report, run_oos_validation

# 약세장 포함 기본 윈도우(헌장 §10② OOS). 정식(전체 히스토리) 데이터가 이 구간을 커버해야 한다.
DEFAULT_WINDOWS = {
    "2018-bear": ("2018-01-01", "2018-12-31"),
    "2020-covid": ("2020-01-01", "2020-12-31"),
    "2022-bear": ("2022-01-01", "2022-12-31"),
    "full-oos": ("2015-01-01", "2024-12-31"),
}


def _parse_windows(specs: list[str]) -> dict[str, tuple[str, str]]:
    """--window NAME=START:END 들을 dict로. 비면 DEFAULT_WINDOWS."""
    if not specs:
        return DEFAULT_WINDOWS
    out: dict[str, tuple[str, str]] = {}
    for spec in specs:
        name, _, rng = spec.partition("=")
        start, _, end = rng.partition(":")
        if not (name and start and end):
            raise SystemExit(f"--window 형식 오류: {spec!r} (예: trial=2025-01-01:2025-12-31)")
        out[name] = (start, end)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="생존편향 없는 OOS 재검증 (수동, 실 데이터)"
    )
    parser.add_argument(
        "--source", choices=("csv", "norgate"), default="csv",
        help="데이터 소스: csv(드롭인) 또는 norgate(SDK 실연동)",
    )
    parser.add_argument("--root", help="[csv] 생존편향 없는 데이터 루트 디렉토리")
    parser.add_argument(
        "--watchlist", default="S&P 500 Current & Past",
        help="[norgate] 상폐 포함 후보 워치리스트",
    )
    parser.add_argument(
        "--extra", nargs="*", default=[],
        help="[norgate] 워치리스트에 더할 섹터 ETF 등 (예: SMH XLE XLF)",
    )
    parser.add_argument(
        "--window", action="append", default=[],
        help="윈도우 재정의 NAME=START:END (반복 가능). 비면 약세장 기본 윈도우.",
    )
    parser.add_argument("--max-risk-pct", type=float, default=0.015, help="보수적 working fraction")
    args = parser.parse_args()

    if args.source == "csv":
        if not args.root:
            raise SystemExit("--source csv 에는 --root 가 필요하다.")
        provider = CsvPointInTimeProvider(args.root)
    else:
        provider = NorgateProvider(
            universe_watchlist=args.watchlist,
            extra_symbols=tuple(args.extra),
        )

    windows = _parse_windows(args.window)
    results = run_oos_validation(provider, windows, max_risk_pct=args.max_risk_pct)
    print(format_oos_report(results))


if __name__ == "__main__":
    main()
