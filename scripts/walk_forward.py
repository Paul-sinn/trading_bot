"""장기/워크포워드 검증 러너 — 잠긴 next-bar-limit 3% 베이스라인을 날짜 윈도우별로 재시뮬한다.

가용 데이터 범위에서 full/yearly/rolling 윈도우를 만들고, 각 윈도우를 start_date/end_date만 바꿔
독립 재시뮬한다(지표는 이전 히스토리까지 사용). 윈도우마다 SPY/QQQ/equal-weight 매수보유와 비교.
갭 가드 미적용. winner extension 미적용. next-open 미사용. 스캐너/디시전/사이징/RiskGate 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/walk_forward.md
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
from agents.walk_forward import (  # noqa: E402
    build_walk_forward,
    compute_walk_forward_summary,
    format_walk_forward,
    generate_windows,
    make_window_result,
)

# 잠긴 현실 베이스라인 (specs/realistic_entry_baseline.md).
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


def _config_to_args(settings, *, symbols=None, start=None, end=None) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols) if symbols else None,
        start_date=start, end_date=end, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _data_range(price_data, benchmark_prices):
    """유니버스 price_data(없으면 benchmark)에서 [min,max] 날짜를 뽑는다. 없으면 (None,None)."""
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


def run_walk_forward(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", symbols=None, events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None, windows=None,
):
    """윈도우 생성 + 윈도우별 잠긴 베이스라인 재시뮬로 워크포워드 검증을 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, symbols=symbols,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    full_args = _config_to_args(settings)
    try:
        price_data, benchmark_prices = run_sim._feature_inputs(full_args)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)

    # 벤치마크(SPY)는 price_data에서 제외되므로 비교용으로만 close 프레임을 주입한다.
    bench_price_data = dict(price_data or {})
    if benchmark_prices is not None and benchmark not in bench_price_data:
        bench_price_data[benchmark] = pd.DataFrame({"close": benchmark_prices})
    bench_universe = [s for s in (price_data or {}) if s != benchmark and s != qqq_symbol]

    data_min, data_max = _data_range(price_data, benchmark_prices)
    if windows is None:
        if data_min is None:
            return None, "데이터 범위를 결정할 수 없음(빈 유니버스)"
        windows = generate_windows(data_min, data_max)

    def _run_window(win):
        args = _config_to_args(settings, start=win.start, end=win.end)
        try:
            res = fn(args)
            perf = res.performance
        except run_sim.DataAdapterError:
            perf = None
        bench = compute_baseline_comparison(
            perf or SimpleNamespace(cumulative_return=0.0, max_drawdown=0.0, equity_curve=None),
            bench_price_data, universe=bench_universe, start=win.start, end=win.end,
            benchmark_symbol=benchmark, qqq_symbol=qqq_symbol,
        )
        return make_window_result(win.label, win.kind, win.start, win.end, perf, bench)

    by_kind: dict[str, list] = {"full": [], "year": [], "roll6": [], "roll12": []}
    for win in windows:
        by_kind.setdefault(win.kind, []).append(_run_window(win))

    full = by_kind["full"][0] if by_kind["full"] else None
    rolling = by_kind["roll6"] + by_kind["roll12"]
    summary = compute_walk_forward_summary(rolling)
    report = build_walk_forward(
        full, by_kind["year"], by_kind["roll6"], by_kind["roll12"], summary,
        data_start=(None if data_min is None else pd.Timestamp(data_min).strftime("%Y-%m-%d")),
        data_end=(None if data_max is None else pd.Timestamp(data_max).strftime("%Y-%m-%d")),
    )
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="장기/워크포워드 검증(next-bar-limit 3%, 실주문 0)")
    p.add_argument("--data-root", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report, error = run_walk_forward(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        symbols=args.symbols, events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_walk_forward(report)
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
