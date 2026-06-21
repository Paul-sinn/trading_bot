"""트레일링 스톱/청산 정책 딥다이브 러너 — `python -m experiments.exit_policy_deep_dive`.

잠긴 베이스라인을 그대로 두고 청산 플래그(stop/trail/max_hold)만 바꾼 true-rerun 변형을 비교한다.
진입 모델/유니버스/스캐너/디시전/사이징/RiskGate 미변경. all_exits_off는 diagnostic only.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음.

spec: specs/exit_deep_dive.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_sim  # noqa: E402
from agents.baseline_comparison import compute_baseline_comparison  # noqa: E402
from agents.exit_deep_dive import (  # noqa: E402
    build_exit_deep_dive,
    format_exit_deep_dive_markdown,
    summarize_variant,
    trailing_impact,
)
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from experiments.universe_bias_test import BASELINE_UNIVERSE, _tradable  # noqa: E402

# 잠긴 현실 베이스라인 (specs/realistic_entry_baseline.md). 진입 잠금 — 변경 금지.
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"

# (name, stop, trail, max_hold, diagnostic_only)
_VARIANTS = (
    ("baseline", 0.15, 0.20, 60, False),
    ("trail_off", 0.15, None, 60, False),
    ("trail_10", 0.15, 0.10, 60, False),
    ("trail_15", 0.15, 0.15, 60, False),
    ("trail_20", 0.15, 0.20, 60, False),
    ("trail_25", 0.15, 0.25, 60, False),
    ("trail_30", 0.15, 0.30, 60, False),
    ("stop_off_trail20", None, 0.20, 60, False),
    ("stop_10_trailoff", 0.10, None, 60, False),
    ("stop_15_trailoff", 0.15, None, 60, False),
    ("stop_20_trailoff", 0.20, None, 60, False),
    ("hold_45_trailoff", 0.15, None, 45, False),
    ("hold_60_trailoff", 0.15, None, 60, False),
    ("hold_75_trailoff", 0.15, None, 75, False),
    ("hold_90_trailoff", 0.15, None, 90, False),
    ("all_exits_off", None, None, None, True),   # diagnostic only
)


def _config_to_args(settings, *, symbols, stop, trail, max_hold) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols), start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=max_hold, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def run_exit_deep_dive(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None,
):
    """청산 변형 true-rerun으로 청산 정책 딥다이브 리포트를 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, qqq_symbol=qqq_symbol,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    probe = _config_to_args(settings, symbols=BASELINE_UNIVERSE, stop=_STOP, trail=_TRAIL, max_hold=_MAX_HOLD)
    probe.symbols = None
    try:
        price_data, benchmark_prices = run_sim._feature_inputs(probe)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)
    available = set(price_data or {})
    syms = _tradable(BASELINE_UNIVERSE, available)

    bench_price_data = dict(price_data or {})
    if benchmark_prices is not None and benchmark not in bench_price_data:
        bench_price_data[benchmark] = pd.DataFrame({"close": benchmark_prices})
    bench_universe = [s for s in syms if s != benchmark and s != qqq_symbol]

    def _bench(performance):
        cmp = compute_baseline_comparison(performance, bench_price_data, universe=bench_universe,
                                          benchmark_symbol=benchmark, qqq_symbol=qqq_symbol)
        def _r(p):
            return next((b.cumulative_return for b in cmp.baselines if b.name.startswith(p)), None)
        return _r("SPY"), _r("QQQ")

    cache: dict[tuple, tuple] = {}   # (stop,trail,hold) → (legs, performance)

    def _rerun(stop, trail, max_hold):
        key = (stop, trail, max_hold)
        if key not in cache:
            args = _config_to_args(settings, symbols=syms, stop=stop, trail=trail, max_hold=max_hold)
            res = fn(args)
            diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(args, res))
            cache[key] = (diag.trades, res.performance)
        return cache[key]

    variants = []
    legs_by_name: dict[str, tuple] = {}
    for name, stop, trail, max_hold, diag_only in _VARIANTS:
        legs, perf = _rerun(stop, trail, max_hold)
        legs_by_name[name] = legs
        spy, qqq = _bench(perf)
        variants.append(summarize_variant(name, (stop, trail, max_hold), legs, perf,
                                          spy=spy, qqq=qqq, diagnostic_only=diag_only))

    hurt, helped = trailing_impact(legs_by_name["baseline"], legs_by_name["trail_off"])
    return build_exit_deep_dive(variants, hurt, helped), None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="청산 정책/트레일링 딥다이브(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default="reports/exit_policy_deep_dive.md")
    return p


def run(args) -> int:
    report, error = run_exit_deep_dive(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_exit_deep_dive_markdown(report)
    print(text)
    if args.output:
        out = Path(args.output)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"리포트 저장: {out}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
