"""일간 섀도 리포트 / 전진 원장 러너 — `python -m experiments.daily_shadow_report [--date YYYY-MM-DD]`.

(1) 최신 거래일 결정 로그 생성, (2) 결정 원장에 ID 중복 없이 append, (3) 성숙한 과거 레코드 채점(미성숙은
pending), (4) 사람용 일간 리포트 작성. signal_decision_log + decision_outcome 러너를 오케스트레이션할 뿐 —
스캐너/디시전/RiskGate/베이스라인 미변경.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/daily_shadow.md
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
from agents.daily_shadow import (  # noqa: E402
    build_daily_shadow,
    format_daily_shadow_markdown,
    merge_decision_ledger,
    record_id,
    upsert_outcome_ledger,
)
from agents.decision_outcome import score_records, summarize_buys  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from experiments.signal_decision_log import build_decision_records  # noqa: E402

# 잠긴 현실 베이스라인(변경 금지) — leg 재구성용.
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
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write_jsonl(path, records):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n",
                 encoding="utf-8")


def run_daily_shadow(*, data_root, benchmark="SPY", date=None, events_csv="data/events.csv",
                     assume_no_events=False, starting_cash=1000.0,
                     decision_ledger="reports/signal_decision_log.jsonl",
                     outcome_ledger="reports/decision_outcome_score.jsonl",
                     daily_md="reports/daily_shadow_report.md",
                     simulate_fn=None, decision_builder=None):
    """일간 워크플로를 멱등하게 실행한다. (report, stats, error)."""
    settings = dict(data_root=data_root, benchmark=benchmark,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)
    build = decision_builder or build_decision_records
    rep_date, today_records, err = build(settings=settings, end=date, simulate_fn=simulate_fn)
    if err is not None:
        return None, None, err

    fn = simulate_fn or run_sim.simulate
    try:
        price_data = run_sim.load_norgate_folder(data_root)
        legs = compute_trade_diagnostics(fn(_sim_args(settings)).multiday).trades
    except run_sim.DataAdapterError as exc:
        return None, None, str(exc)

    # 결정 원장: ID 미존재만 append(멱등).
    existing_dec = _read_jsonl(decision_ledger)
    merged_dec, added = merge_decision_ledger(existing_dec, [r.to_dict() for r in today_records])
    _write_jsonl(decision_ledger, merged_dec)

    # 전체 원장 채점.
    scored = score_records(merged_dec, price_data, legs)

    # 결과 원장: ID upsert(성숙 갱신). newly matured는 기존 대비 diff.
    existing_out = _read_jsonl(outcome_ledger)
    existing_by_id = {record_id(r): r for r in existing_out}
    new_out = [s.to_dict() for s in scored if s.outcome.scorable]
    _write_jsonl(outcome_ledger, upsert_outcome_ledger(existing_out, new_out))

    buy_summary = summarize_buys(scored)
    report = build_daily_shadow(rep_date, today_records, scored, existing_by_id, buy_summary=buy_summary)

    text = format_daily_shadow_markdown(report)
    if daily_md:
        out = Path(daily_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")

    stats = dict(date=rep_date, added=added, scored=len(scored),
                 pending60=report.pending_counts.get(60, 0), matured60=report.matured_counts.get(60, 0))
    return report, stats, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="일간 섀도 리포트/전진 원장(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--date", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--decision-ledger", default="reports/signal_decision_log.jsonl")
    p.add_argument("--outcome-ledger", default="reports/decision_outcome_score.jsonl")
    p.add_argument("--output-md", default="reports/daily_shadow_report.md")
    return p


def run(args) -> int:
    report, stats, error = run_daily_shadow(
        data_root=args.data_root, benchmark=args.benchmark, date=args.date,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
        decision_ledger=args.decision_ledger, outcome_ledger=args.outcome_ledger, daily_md=args.output_md,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    print(format_daily_shadow_markdown(report))
    print(f"\n[stats] date={stats['date']} appended={stats['added']} scored={stats['scored']} "
          f"matured60={stats['matured60']} pending60={stats['pending60']}")
    print(f"일간 리포트 저장: {args.output_md}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
