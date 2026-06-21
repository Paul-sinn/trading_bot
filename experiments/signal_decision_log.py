"""시그널 결정 로그 러너 — `python -m experiments.signal_decision_log [--date YYYY-MM-DD]`.

선택 날짜(또는 최신 거래일)의 BUY/REJECT/SKIP 결정을 기존 dry-run 산출물에서 읽어 md + jsonl로 기록한다.
run_sim.simulate를 잠긴 베이스라인으로 end_date까지 돌려 마지막 day_result의 결정 행을 읽을 뿐 —
스캐너/디시전/사이징/RiskGate/베이스라인/유니버스를 바꾸지 않는다. JSONL은 append(전진 검증 누적).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/decision_log.md
"""

from __future__ import annotations

import argparse
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
from agents.decision_log import (  # noqa: E402
    build_decision_log,
    format_decision_log_markdown,
    make_record,
    records_to_jsonl,
)
from agents.feature_diagnostics import compute_feature_diagnostics  # noqa: E402
from agents.feature_shadow_score import _score as shadow_score  # noqa: E402

# 잠긴 현실 베이스라인(변경 금지) — 누적 포지션이 실제 전략을 반영하도록 동일 설정으로 시뮬.
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


def _config_to_args(settings, *, end) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"], symbols=None,
        start_date=None, end_date=end, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _shares(pos):
    if pos is None:
        return 0.0
    for attr in ("shares", "quantity", "qty"):
        v = getattr(pos, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
    try:
        return float(pos)
    except (TypeError, ValueError):
        return 0.0


def build_decision_records(*, simulate_fn=None, settings, end=None):
    """end_date까지 시뮬해 마지막 거래일의 결정 레코드를 만든다. (date, records, error)."""
    fn = simulate_fn or run_sim.simulate
    args = _config_to_args(settings, end=end)
    try:
        res = fn(args)
        price_data, benchmark_prices = run_sim._feature_inputs(args)
    except run_sim.DataAdapterError as exc:
        return None, None, str(exc)

    day_results = getattr(res.multiday, "day_results", ())
    if not day_results:
        return None, None, "거래일 결과 없음(데이터 구간/warmup 확인)"
    last = day_results[-1]
    report = last.report
    report_date = report.report_date

    universe = list(price_data or {})
    feat = compute_feature_diagnostics(
        res.multiday, price_data, benchmark_prices=benchmark_prices,
        source_trades=[SimpleNamespace(symbol=s, entry_date=report_date) for s in universe],
    )
    snap_index = {(r.symbol, r.context_date): r.snapshot for r in feat.rows}

    positions = {}
    portfolio = getattr(res.multiday, "portfolio", None) or getattr(last, "portfolio", None)
    if portfolio is not None:
        positions = getattr(portfolio, "positions", {}) or {}

    records = []
    scanned = set()
    for d in report.decisions:
        scanned.add(d.symbol)
        decision = "BUY" if d.effective_decision is Decision.BUY else "REJECT"
        snap = snap_index.get((d.symbol, report_date))
        records.append(make_record(
            report_date, d.symbol, decision, reason=d.rationale, snapshot=snap,
            shadow_score=(shadow_score(snap) if snap is not None else None),
            riskgate_passed=d.veto.passed, riskgate_reasons=tuple(d.veto.reasons),
            position_shares=_shares(positions.get(d.symbol)),
        ))
    for s in universe:
        if s in scanned:
            continue
        snap = snap_index.get((s, report_date))
        records.append(make_record(
            report_date, s, "SKIP", reason="스캐너 후보 미선정(필터 미통과)", snapshot=snap,
            shadow_score=(shadow_score(snap) if snap is not None else None),
            riskgate_passed=None, riskgate_reasons=(), position_shares=_shares(positions.get(s)),
        ))
    return report_date, records, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="시그널 결정 로그(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--date", default=None, help="기록할 날짜(YYYY-MM-DD). 생략 시 최신 거래일.")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output-md", default="reports/signal_decision_log.md")
    p.add_argument("--output-jsonl", default="reports/signal_decision_log.jsonl")
    return p


def run(args) -> int:
    settings = dict(data_root=args.data_root, benchmark=args.benchmark,
                    events_csv=(None if args.assume_no_events else args.events_csv),
                    assume_no_events=args.assume_no_events, starting_cash=args.starting_cash)
    date, records, error = build_decision_records(settings=settings, end=args.date)
    if error is not None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)

    log = build_decision_log(date, records)
    text = format_decision_log_markdown(log)
    print(text)

    if args.output_md:
        out = Path(args.output_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"마크다운 저장: {out}")
    if args.output_jsonl:
        out = Path(args.output_jsonl)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:      # append-friendly(전진 검증 누적)
            fh.write(records_to_jsonl(records) + "\n")
        print(f"JSONL append: {out}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
