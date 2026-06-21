"""현실 베이스라인 강건성 리포트 러너 — 잠긴 next-bar-limit 3% 풀런 + LOO 재시뮬로 전략을 검증한다.

60일 잠긴 베이스라인을 고정하고 run_sim.simulate를 돌린다. LOO는 심볼을 하나씩 빼고 잠긴 베이스라인
재시뮬. 갭 가드 미적용. winner extension 미적용. next-open 미사용. 스캐너/디시전/사이징/RiskGate 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/baseline_robustness.md
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
from agents.baseline_robustness import (  # noqa: E402
    build_baseline_robustness,
    compute_concentration,
    compute_exit_reason_distribution,
    compute_full_result,
    compute_slippage_stress,
    format_baseline_robustness,
)
from agents.robustness_report import compute_robustness_report  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402

# 잠긴 현실 베이스라인 (specs/realistic_entry_baseline.md).
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"
_SLIPPAGES = (0.0, 0.0025, 0.005, 0.01)


def _config_to_args(settings, symbols) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols) if symbols else None,
        start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def run_baseline_robustness(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", symbols=None, events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, do_loo=True, simulate_fn=None,
):
    """잠긴 베이스라인 풀런 + LOO 재시뮬로 강건성 리포트를 만든다. 데이터 실패는 (None, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, symbols=symbols,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)
    base_args = _config_to_args(settings, symbols)
    try:
        res = fn(base_args)
        price_data, benchmark_prices = run_sim._feature_inputs(base_args)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)

    diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(base_args, res))

    rerun_results: dict[str, object] = {}
    if do_loo:
        universe = [s for s in price_data if s != benchmark and s != qqq_symbol]
        for s in universe:
            drop_syms = [x for x in price_data if x != s]
            if not drop_syms:
                continue
            try:
                rerun_results[s] = fn(_config_to_args(settings, drop_syms))
            except run_sim.DataAdapterError:
                continue

    robustness = compute_robustness_report(res.multiday, price_data, trade_diag=diag,
                                           rerun_results=rerun_results)
    bench_universe = [s for s in price_data if s != benchmark and s != qqq_symbol]
    # 벤치마크(SPY)는 price_data에서 제외되어 들어오므로 비교용으로만 close 프레임을 주입한다.
    bench_price_data = dict(price_data)
    if benchmark_prices is not None and benchmark not in bench_price_data:
        bench_price_data[benchmark] = pd.DataFrame({"close": benchmark_prices})
    benchmark_cmp = compute_baseline_comparison(
        res.performance, bench_price_data, universe=bench_universe,
        benchmark_symbol=benchmark, qqq_symbol=qqq_symbol,
    )

    full = compute_full_result(res.performance)
    slippage = compute_slippage_stress(diag, slippages=_SLIPPAGES, starting_cash=starting_cash)
    exit_reasons = compute_exit_reason_distribution(diag)
    sym_totals = {p.symbol: p.total_pnl for p in robustness.symbol_perf}
    concentration = compute_concentration(sym_totals)

    report = build_baseline_robustness(full, robustness, benchmark_cmp, slippage,
                                       exit_reasons, concentration)
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="현실 베이스라인 강건성 리포트(next-bar-limit 3%, 실주문 0)")
    p.add_argument("--data-root", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--no-loo", action="store_true", help="leave-one-symbol-out 재시뮬 생략")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report, error = run_baseline_robustness(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        symbols=args.symbols, events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
        do_loo=not args.no_loo,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_baseline_robustness(report)
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
