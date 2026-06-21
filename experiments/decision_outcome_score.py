"""결정 결과 채점 / 전진 검증 러너 — `python -m experiments.decision_outcome_score [--backfill]`.

결정 로그(JSONL) 또는 --backfill(historical sim day_results 결정)을 로컬 OHLCV 미래 가격으로 사후 채점한다.
스캐너/디시전/RiskGate/베이스라인 미변경. 재진입 컨텍스트는 시뮬 leg에서 report-only로 재구성. forward만 사용.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/decision_outcome.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_sim  # noqa: E402
from agents.decision import Decision  # noqa: E402
from agents.decision_outcome import (  # noqa: E402
    build_outcome_report,
    format_outcome_markdown,
    score_records,
    scored_to_jsonl,
)
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402

# 잠긴 현실 베이스라인(변경 금지) — leg/backfill 재구성용.
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


def _sim_args(settings) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"], symbols=None,
        start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _read_jsonl(path) -> list[dict]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _backfill_records(multiday) -> list[dict]:
    """historical sim의 일별 결정(BUY/REJECT)을 채점용 레코드로 재구성한다."""
    records = []
    for day in getattr(multiday, "day_results", ()):
        report = getattr(day, "report", None)
        if report is None:
            continue
        date = report.report_date
        for d in report.decisions:
            decision = "BUY" if d.effective_decision is Decision.BUY else "REJECT"
            reason = d.rationale + (("|VETO: " + "; ".join(d.veto.reasons)) if not d.veto.passed else "")
            records.append({"date": date, "symbol": d.symbol, "decision": decision, "reason": reason})
    return records


def run_decision_outcome(*, data_root, benchmark="SPY", jsonl_path=None, backfill=False,
                         from_date=None, to_date=None, events_csv="data/events.csv",
                         assume_no_events=False, starting_cash=1000.0, simulate_fn=None):
    """레코드(로그 또는 backfill)를 미래 가격으로 채점해 리포트를 만든다. (report, scored, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, events_csv=events_csv,
                    assume_no_events=assume_no_events, starting_cash=starting_cash)
    args = _sim_args(settings)
    try:
        price_data = run_sim.load_norgate_folder(data_root)
        res = fn(args)
    except run_sim.DataAdapterError as exc:
        return None, None, str(exc)

    legs = compute_trade_diagnostics(res.multiday).trades

    if backfill:
        records = _backfill_records(res.multiday)
    else:
        if not jsonl_path or not Path(jsonl_path).exists():
            return None, None, f"JSONL 없음: {jsonl_path} (--backfill 사용 가능)"
        records = _read_jsonl(jsonl_path)

    scored = score_records(records, price_data, legs, from_date=from_date, to_date=to_date)
    report = build_outcome_report(scored, in_sample=backfill)
    return report, scored, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="결정 결과 채점/전진 검증(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--jsonl", default="reports/signal_decision_log.jsonl")
    p.add_argument("--backfill", action="store_true", help="historical sim 결정으로 채점(로그 비었을 때)")
    p.add_argument("--from-date", default=None)
    p.add_argument("--to-date", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output-md", default="reports/decision_outcome_score.md")
    p.add_argument("--output-jsonl", default="reports/decision_outcome_score.jsonl")
    return p


def run(args) -> int:
    report, scored, error = run_decision_outcome(
        data_root=args.data_root, benchmark=args.benchmark, jsonl_path=args.jsonl,
        backfill=args.backfill, from_date=args.from_date, to_date=args.to_date,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_outcome_markdown(report)
    print(text)
    if args.output_md:
        out = Path(args.output_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"마크다운 저장: {out}")
    if args.output_jsonl:
        out = Path(args.output_jsonl)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(scored_to_jsonl(scored) + "\n", encoding="utf-8")
        print(f"JSONL 저장: {out}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
