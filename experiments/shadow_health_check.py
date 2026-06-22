"""섀도 런 헬스 체크 러너 — `python -m experiments.shadow_health_check [--date YYYY-MM-DD]`.

로컬 OHLCV + 결정/결과 원장을 검증해 PASS/WARN/FAIL을 낸다. 데이터·원장 읽기만 — 스캐너/디시전/
RiskGate/베이스라인 미변경. md + json 출력.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/shadow_health.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_sim  # noqa: E402
from agents.shadow_health import build_health, format_health_markdown, health_to_json  # noqa: E402
from experiments.universe_bias_test import BASELINE_UNIVERSE  # noqa: E402

_AUX = {"SPY", "QQQ", "VIX"}


def _read_jsonl_with_malformed(path):
    """(parsed_records, malformed_count). 파일 없으면 ([], 0)."""
    p = Path(path)
    if not p.exists():
        return [], 0
    parsed, malformed = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                parsed.append(obj)
            else:
                malformed += 1
        except json.JSONDecodeError:
            malformed += 1
    return parsed, malformed


def _data_facts(price_data):
    """심볼별 최신 날짜 + 거래일 집합(보조 심볼 제외)."""
    last_dates, trading_days = {}, set()
    for sym, df in (price_data or {}).items():
        if sym in _AUX:
            continue
        try:
            idx = df.index
            if len(idx) == 0:
                continue
            last_dates[sym] = idx.max().strftime("%Y-%m-%d")
            trading_days.update(d.strftime("%Y-%m-%d") for d in idx)
        except (AttributeError, ValueError):
            continue
    return last_dates, trading_days


def run_shadow_health(*, data_root, date=None, as_of=None, stale_days=5,
                      decision_ledger="reports/signal_decision_log.jsonl",
                      outcome_ledger="reports/decision_outcome_score.jsonl"):
    """데이터 + 원장을 점검해 HealthReport를 만든다. (report, error)."""
    try:
        price_data = run_sim.load_norgate_folder(data_root)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)

    last_dates, trading_days = _data_facts(price_data)
    available = [s for s in price_data if s not in _AUX]
    dec_records, dec_malformed = _read_jsonl_with_malformed(decision_ledger)
    out_records, out_malformed = _read_jsonl_with_malformed(outcome_ledger)

    report = build_health(
        universe=BASELINE_UNIVERSE, available_symbols=available, last_dates=last_dates,
        report_date=date, trading_days=trading_days, as_of=as_of,
        decision_records=dec_records, decision_malformed=dec_malformed,
        outcome_records=out_records, outcome_malformed=out_malformed, stale_days=stale_days,
    )
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="섀도 런 헬스 체크(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--date", default=None, help="검증할 report date(거래일 여부 확인)")
    p.add_argument("--as-of", default=None, help="신선도 기준 날짜(생략 시 최신 데이터)")
    p.add_argument("--stale-days", type=int, default=5)
    p.add_argument("--decision-ledger", default="reports/signal_decision_log.jsonl")
    p.add_argument("--outcome-ledger", default="reports/decision_outcome_score.jsonl")
    p.add_argument("--output-md", default="reports/shadow_health_check.md")
    p.add_argument("--output-json", default="reports/shadow_health_check.json")
    return p


def run(args) -> int:
    report, error = run_shadow_health(
        data_root=args.data_root, date=args.date, as_of=args.as_of, stale_days=args.stale_days,
        decision_ledger=args.decision_ledger, outcome_ledger=args.outcome_ledger,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    print(format_health_markdown(report))
    if args.output_md:
        out = Path(args.output_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(format_health_markdown(report) + "\n", encoding="utf-8")
        print(f"마크다운 저장: {out}")
    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(health_to_json(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 저장: {out}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
