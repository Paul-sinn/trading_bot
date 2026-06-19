"""생존편향 없는 OOS 재검증 CLI (수동 — 사용자 CSV/Parquet 데이터 필요, CI 아님).

헌장 §3/§10②: 생존편향 없는 벤더 데이터(상폐종목+시점별 지수편입)로 약세장 포함 OOS 재검증.
⚠️ 실 데이터는 사용자가 준비한다(Norgate/Sharadar export → CSV). 아래 구조로 <root>에 둔다:
    <root>/metrics.csv         — symbol,listed_from,delisted_at,avg_dollar_volume,atr_pct,is_leveraged_or_inverse
    <root>/ohlcv/<SYMBOL>.csv  — date,open,high,low,close,volume (상폐종목 포함)
    <root>/vix.csv             — date,close

사용:
    .venv/bin/python scripts/run_oos.py --root data/survivorship_free

집계·러너 로직은 agents/oos_validate.py(테스트됨). 이 파일은 얇은 CLI다. go/no-go는 사람.
"""

from __future__ import annotations

import argparse

from agents.data_adapter import CsvPointInTimeProvider
from agents.oos_validate import format_oos_report, run_oos_validation

# 약세장 포함 기본 윈도우(헌장 §10② OOS). --root 데이터가 이 구간을 커버해야 한다.
DEFAULT_WINDOWS = {
    "2018-bear": ("2018-01-01", "2018-12-31"),
    "2020-covid": ("2020-01-01", "2020-12-31"),
    "2022-bear": ("2022-01-01", "2022-12-31"),
    "full-oos": ("2015-01-01", "2024-12-31"),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="생존편향 없는 OOS 재검증 (수동, 사용자 CSV 데이터)"
    )
    parser.add_argument("--root", required=True, help="생존편향 없는 데이터 루트 디렉토리")
    parser.add_argument("--max-risk-pct", type=float, default=0.015, help="보수적 working fraction")
    args = parser.parse_args()

    provider = CsvPointInTimeProvider(args.root)
    results = run_oos_validation(
        provider, DEFAULT_WINDOWS, max_risk_pct=args.max_risk_pct
    )
    print(format_oos_report(results))


if __name__ == "__main__":
    main()
