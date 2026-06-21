"""후보 청산 정책 검증 러너 — `python -m experiments.exit_candidate_validation`.

잠긴 베이스라인을 그대로 두고 hold_45_trailoff 후보(+선택 alt)가 강건한지 검증한다. 정책별로 풀런 +
LOO + no_MU/no_ARM/no_top3 + 슬리피지 + 벤치마크. 청산 플래그만 바꾼 true-rerun. 진입/유니버스/
스캐너/디시전/사이징/RiskGate 미변경. **베이스라인 승격 없음.**

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음.

spec: specs/exit_candidate.md
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
from agents.baseline_robustness import compute_slippage_stress  # noqa: E402
from agents.exit_candidate import (  # noqa: E402
    PolicyValidation,
    build_candidate_validation,
    format_candidate_validation_markdown,
    make_drop,
    positive_active_quarters,
    yearly_pnl,
)
from agents.exit_deep_dive import summarize_variant  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from agents.universe_bias import compute_top_shares  # noqa: E402
from experiments.universe_bias_test import BASELINE_UNIVERSE, _tradable  # noqa: E402

# 잠긴 진입(변경 금지).
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_SHARE_MODE = "fractional"
_SLIPPAGES = (0.005, 0.01)

# (name, stop, trail, max_hold, do_loo)
_POLICIES = (
    ("locked_baseline", 0.15, 0.20, 60, True),
    ("candidate", 0.15, None, 45, True),
    ("alt_candidate", 0.20, None, 60, False),
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


def run_exit_candidate_validation(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None,
):
    """정책별 풀런+LOO+심볼제거+슬리피지로 후보 검증 리포트를 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, qqq_symbol=qqq_symbol,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    probe = _config_to_args(settings, symbols=BASELINE_UNIVERSE, stop=0.15, trail=0.20, max_hold=60)
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

    cache: dict[tuple, tuple] = {}   # (stop,trail,hold,frozenset(symbols)) → (diag, perf)

    def _rerun(stop, trail, max_hold, symbols):
        key = (stop, trail, max_hold, frozenset(symbols))
        if key not in cache:
            args = _config_to_args(settings, symbols=symbols, stop=stop, trail=trail, max_hold=max_hold)
            res = fn(args)
            diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(args, res))
            cache[key] = (diag, res.performance)
        return cache[key]

    def _bench(perf):
        cmp = compute_baseline_comparison(perf, bench_price_data, universe=bench_universe,
                                          benchmark_symbol=benchmark, qqq_symbol=qqq_symbol)
        def _r(p):
            return next((b.cumulative_return for b in cmp.baselines if b.name.startswith(p)), None)
        return _r("SPY"), _r("QQQ"), _r("equal-weight")

    policies = []
    for name, stop, trail, max_hold, do_loo in _POLICIES:
        diag, perf = _rerun(stop, trail, max_hold, syms)
        spy, qqq, eq = _bench(perf)
        full = summarize_variant(name, (stop, trail, max_hold), diag.trades, perf, spy=spy, qqq=qqq)
        slippage = compute_slippage_stress(diag, slippages=_SLIPPAGES, starting_cash=starting_cash)

        def _drop(label, drop_syms):
            d_diag, d_perf = _rerun(stop, trail, max_hold, drop_syms)
            return make_drop(label, full.total_pnl, d_perf.total_pnl, d_perf.cumulative_return)

        no_mu = _drop("no_MU", [s for s in syms if s != "MU"]) if "MU" in syms else None
        no_arm = _drop("no_ARM", [s for s in syms if s != "ARM"]) if "ARM" in syms else None
        top3 = set(compute_top_shares(
            [SimpleNamespace(symbol=s, total_pnl=p)
             for s, p in _sym_totals(diag.trades).items()])[2])
        no_top3 = _drop("no_top3", [s for s in syms if s not in top3]) if top3 else None

        loo_objs, worst = (), None
        if do_loo:
            loo_list = []
            for s in syms:
                _d, d_perf = _rerun(stop, trail, max_hold, [x for x in syms if x != s])
                diff = (d_perf.total_pnl - full.total_pnl
                        if (d_perf.total_pnl is not None and full.total_pnl is not None) else None)
                loo_list.append(SimpleNamespace(
                    excluded_symbol=s, total_pnl=d_perf.total_pnl, total_pnl_diff=diff,
                    return_pct=d_perf.cumulative_return, max_drawdown=d_perf.max_drawdown, mode="rerun"))
            loo_objs = tuple(loo_list)
            worst = min(loo_objs, key=lambda l: l.total_pnl) if loo_objs else None

        pos_q, act_q = positive_active_quarters(full.quarterly)
        policies.append(PolicyValidation(
            name=name, stop=stop, trail=trail, max_hold=max_hold, full=full, eq_return=eq,
            yearly=yearly_pnl(diag.trades), positive_quarters=pos_q, active_quarters=act_q,
            slippage=slippage, loo=loo_objs, worst_drop=worst,
            no_mu=no_mu, no_arm=no_arm, no_top3=no_top3,
        ))

    report = build_candidate_validation(policies, baseline_name="locked_baseline",
                                        candidate_name="candidate")
    return report, None


def _sym_totals(legs):
    totals: dict[str, float] = {}
    for l in legs:
        if l.pnl is None:
            continue
        totals[l.symbol] = totals.get(l.symbol, 0.0) + l.pnl
    return totals


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="후보 청산 정책 검증(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default="reports/exit_candidate_validation.md")
    return p


def run(args) -> int:
    report, error = run_exit_candidate_validation(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_candidate_validation_markdown(report)
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
