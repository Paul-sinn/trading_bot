"""시그널 제거(ablation) 테스트 러너 — `python -m experiments.signal_ablation_test`.

잠긴 베이스라인을 그대로 두고 "하나씩 제거" 변형과 비교한다. 청산/심볼 변형은 run_sim 플래그만 바꾼
true-rerun. 모멘텀/볼륨/추세는 스캐너/디시전/RiskGate를 바꿔야 진짜 제거되므로 절대 바꾸지 않고,
실현 트레이드를 진입 피처로 제거하는 shadow 근사로만 본다(명확히 표기). 기본 유니버스/주말청산 기본값 불변.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음.

spec: specs/signal_ablation.md
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
from agents.feature_diagnostics import compute_feature_diagnostics  # noqa: E402
from agents.signal_ablation import (  # noqa: E402
    MODE_SHADOW,
    MODE_TRUE,
    build_ablation,
    format_ablation_markdown,
    shadow_drop,
    summarize,
)
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from agents.universe_bias import compute_top_shares  # noqa: E402
# 잠긴 기본 주식 유니버스/거래가능 필터를 단일 출처에서 재사용(중복 정의 금지).
from experiments.universe_bias_test import (  # noqa: E402
    BASELINE_UNIVERSE,
    LEVERAGED_ETFS,
    _tradable,
)

# 잠긴 현실 베이스라인 (specs/realistic_entry_baseline.md). 변경 금지.
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


def _config_to_args(settings, *, symbols, stop=_STOP, trail=_TRAIL, max_hold=_MAX_HOLD) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols), start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=max_hold, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _bench_returns(performance, bench_price_data, universe, settings):
    cmp = compute_baseline_comparison(
        performance, bench_price_data, universe=universe,
        benchmark_symbol=settings["benchmark"], qqq_symbol=settings.get("qqq_symbol", "QQQ"),
    )
    def _r(prefix):
        return next((b.cumulative_return for b in cmp.baselines if b.name.startswith(prefix)), None)
    return _r("SPY"), _r("QQQ"), _r("equal-weight")


def run_signal_ablation(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None,
):
    """청산/심볼 true-rerun + 모멘텀/볼륨/추세 shadow 근사로 ablation 리포트를 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, qqq_symbol=qqq_symbol,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    probe = _config_to_args(settings, symbols=BASELINE_UNIVERSE)
    probe.symbols = None
    try:
        price_data, benchmark_prices = run_sim._feature_inputs(probe)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)
    available = set(price_data or {})
    base_syms = _tradable(BASELINE_UNIVERSE, available)

    bench_price_data = dict(price_data or {})
    if benchmark_prices is not None and benchmark not in bench_price_data:
        bench_price_data[benchmark] = pd.DataFrame({"close": benchmark_prices})
    bench_universe = [s for s in (price_data or {}) if s != benchmark and s != qqq_symbol]

    def _legs_perf(args):
        res = fn(args)
        diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(args, res))
        return res, diag

    def _true(name, *, symbols=None, stop=_STOP, trail=_TRAIL, max_hold=_MAX_HOLD):
        syms = symbols if symbols is not None else base_syms
        args = _config_to_args(settings, symbols=syms, stop=stop, trail=trail, max_hold=max_hold)
        res, diag = _legs_perf(args)
        spy, qqq, eq = _bench_returns(res.performance, bench_price_data,
                                      [s for s in syms if s in bench_universe], settings)
        return summarize(name, MODE_TRUE, diag.trades, starting_cash=starting_cash,
                         performance=res.performance, spy=spy, qqq=qqq, eq=eq)

    # --- baseline (이후 shadow/벤치 기준) ---
    base_args = _config_to_args(settings, symbols=base_syms)
    base_res, base_diag = _legs_perf(base_args)
    base_spy, base_qqq, base_eq = _bench_returns(base_res.performance, bench_price_data,
                                                 [s for s in base_syms if s in bench_universe], settings)
    base_result = summarize("baseline", MODE_TRUE, base_diag.trades, starting_cash=starting_cash,
                            performance=base_res.performance, spy=base_spy, qqq=base_qqq, eq=base_eq)

    top1, _s1, top3, _s3, _b, _w = compute_top_shares(
        [SimpleNamespace(symbol=s, total_pnl=p) for s, p in _sym_totals(base_diag.trades).items()])
    top3_set = set(top3)

    variants = [base_result]
    # --- 청산 true-rerun ---
    variants.append(_true("no_stop_loss", stop=None))
    variants.append(_true("no_trailing_stop", trail=None))
    variants.append(_true("no_time_stop", max_hold=None))
    variants.append(_true("no_stop_no_trailing", stop=None, trail=None))
    variants.append(_true("no_exit_controls", stop=None, trail=None, max_hold=None))
    # --- 심볼 true-rerun ---
    variants.append(_true("no_MU", symbols=[s for s in base_syms if s != "MU"]))
    variants.append(_true("no_top3_symbols", symbols=[s for s in base_syms if s not in top3_set]))

    # --- shadow 근사(모멘텀/볼륨/추세) ---
    feat = compute_feature_diagnostics(base_res.multiday, price_data,
                                       benchmark_prices=benchmark_prices, source_trades=base_diag.trades)
    snap_index = {(r.symbol, r.context_date): r.snapshot for r in feat.rows}
    shadow_note = "shadow: 실현 트레이드를 진입 피처로 제거(진짜 재시뮬 아님, MDD n/a)"

    def _shadow(name, feature, *, is_flag=False):
        kept = shadow_drop(base_diag.trades, snap_index, feature, is_flag=is_flag)
        return summarize(name, MODE_SHADOW, kept, starting_cash=starting_cash,
                         spy=base_spy, qqq=base_qqq, eq=base_eq, note=shadow_note)

    variants.append(_shadow("shadow_drop_low_momentum", "momentum_score"))
    variants.append(_shadow("shadow_drop_low_volume", "volume_ratio_20d"))
    variants.append(_shadow("shadow_drop_non_uptrend", "price_above_20ma", is_flag=True))

    return build_ablation(variants), None


def _sym_totals(legs):
    totals: dict[str, float] = {}
    for l in legs:
        if l.pnl is None:
            continue
        totals[l.symbol] = totals.get(l.symbol, 0.0) + l.pnl
    return totals


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="시그널 제거(ablation) 테스트(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default="reports/signal_ablation_test.md")
    return p


def run(args) -> int:
    report, error = run_signal_ablation(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_ablation_markdown(report)
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
