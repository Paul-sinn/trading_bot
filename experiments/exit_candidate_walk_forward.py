"""후보 청산 정책 워크포워드 검증 러너 — `python -m experiments.exit_candidate_walk_forward`.

잠긴 베이스라인 vs 후보(hold_45_trailoff)를 year/quarter/roll3/roll6/roll12 윈도우로 비교한다. 윈도우별로
start_date/end_date + 청산 플래그만 바꾼 true-rerun. 진입/유니버스/스캐너/디시전/사이징/RiskGate 미변경.
강세장 밖 데이터가 없으면 OUT_OF_BULL_VALIDATION = NOT_AVAILABLE로 정직하게 표기. **베이스라인 승격 없음.**

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음.

spec: specs/exit_walk_forward.md
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
from agents.exit_deep_dive import summarize_variant  # noqa: E402
from agents.exit_walk_forward import (  # noqa: E402
    PolicyWindow,
    build_exit_walk_forward,
    compute_stability_verdict,
    compute_window_compares,
    format_exit_walk_forward_markdown,
    generate_exit_windows,
)
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from experiments.universe_bias_test import BASELINE_UNIVERSE, _tradable  # noqa: E402

_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_SHARE_MODE = "fractional"

# (name, stop, trail, max_hold)
_POLICIES = (
    ("locked_baseline", 0.15, 0.20, 60),
    ("candidate", 0.15, None, 45),
    ("alt_candidate", 0.20, None, 60),
)


def _config_to_args(settings, *, symbols, stop, trail, max_hold, start, end) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols), start_date=start, end_date=end, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=max_hold, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _data_range(price_data, benchmark_prices):
    starts, ends = [], []
    for df in (price_data or {}).values():
        try:
            starts.append(df.index.min())
            ends.append(df.index.max())
        except (AttributeError, ValueError):
            continue
    if not starts and benchmark_prices is not None:
        starts.append(benchmark_prices.index.min())
        ends.append(benchmark_prices.index.max())
    if not starts:
        return None, None
    return min(starts), max(ends)


def run_exit_candidate_walk_forward(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None, windows=None,
):
    """정책×윈도우 재시뮬로 후보 워크포워드 검증을 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, qqq_symbol=qqq_symbol,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    probe = _config_to_args(settings, symbols=BASELINE_UNIVERSE, stop=0.15, trail=0.20,
                            max_hold=60, start=None, end=None)
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

    data_min, data_max = _data_range(price_data, benchmark_prices)
    if windows is None:
        if data_min is None:
            return None, "데이터 범위를 결정할 수 없음(빈 유니버스)"
        windows = generate_exit_windows(data_min, data_max)

    cache: dict[tuple, tuple] = {}

    def _rerun(stop, trail, max_hold, start, end):
        key = (stop, trail, max_hold, start, end)
        if key not in cache:
            args = _config_to_args(settings, symbols=syms, stop=stop, trail=trail, max_hold=max_hold,
                                   start=start, end=end)
            res = fn(args)
            diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(args, res))
            cache[key] = (diag, res.performance)
        return cache[key]

    def _bench(perf, start, end):
        cmp = compute_baseline_comparison(perf, bench_price_data, universe=bench_universe,
                                          start=start, end=end,
                                          benchmark_symbol=benchmark, qqq_symbol=qqq_symbol)
        def _r(p):
            return next((b.cumulative_return for b in cmp.baselines if b.name.startswith(p)), None)
        return _r("SPY"), _r("QQQ"), _r("equal-weight")

    policy_windows: list[PolicyWindow] = []
    by_policy: dict[str, list[PolicyWindow]] = {name: [] for name, *_ in _POLICIES}
    for win in windows:
        for name, stop, trail, max_hold in _POLICIES:
            diag, perf = _rerun(stop, trail, max_hold, win.start, win.end)
            spy, qqq, eq = _bench(perf, win.start, win.end)
            result = summarize_variant(name, (stop, trail, max_hold), diag.trades, perf, spy=spy, qqq=qqq)
            pw = PolicyWindow(label=win.label, kind=win.kind, start=win.start, end=win.end,
                              policy=name, result=result, eq_return=eq)
            policy_windows.append(pw)
            by_policy[name].append(pw)

    compares = compute_window_compares(by_policy["locked_baseline"], by_policy["candidate"])
    verdict = compute_stability_verdict(compares)
    report = build_exit_walk_forward(
        policy_windows, compares, verdict,
        data_start=(None if data_min is None else pd.Timestamp(data_min).strftime("%Y-%m-%d")),
        data_end=(None if data_max is None else pd.Timestamp(data_max).strftime("%Y-%m-%d")),
    )
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="후보 청산 정책 워크포워드 검증(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default="reports/exit_candidate_walk_forward.md")
    return p


def run(args) -> int:
    report, error = run_exit_candidate_walk_forward(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_exit_walk_forward_markdown(report)
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
