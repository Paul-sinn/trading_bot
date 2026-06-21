"""유니버스 확장/편향 테스트 러너 — `python -m experiments.universe_bias_test`.

잠긴 베이스라인 파라미터(next-bar-limit 3%, 60일)를 그대로 두고 유니버스만 바꿔 비교한다:
baseline / expanded(40-60 후보) / expanded_no_mu / expanded_no_top3. 로컬 데이터에 없는 심볼은
스킵·리포트. 레버리지 ETF 미혼합. 스캐너/디시전/사이징/RiskGate·기본 유니버스 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/universe_bias.md
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
from agents.robustness_report import compute_robustness_report  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402
from agents.universe_bias import (  # noqa: E402
    build_universe_bias,
    format_universe_bias_markdown,
    summarize_universe,
)

# 잠긴 현실 베이스라인 (specs/realistic_entry_baseline.md). 변경 금지.
_FILL_MODEL = "next-bar-limit"
_BUFFER = 0.03
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"

# 거래 유니버스에서 항상 제외(벤치마크/컴퍼스/보조).
_AUX = ("SPY", "QQQ", "VIX")
# 레버리지 ETF — 이 일반 유니버스 테스트에 절대 섞지 않는다.
LEVERAGED_ETFS = frozenset({"TQQQ", "SOXL", "UPRO", "SQQQ", "FNGU", "TECL", "SPXL", "TNA", "LABU"})

# 잠긴 기본 주식 유니버스(레버리지 ETF 미포함).
BASELINE_UNIVERSE = (
    "NVDA", "AMD", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA",
    "AVGO", "SMCI", "ARM", "MU", "TSM", "ASML", "NFLX", "ORCL", "CRM", "PLTR",
)

# 확장 실험 후보(40-60). 반도체 + 소프트웨어 + AI 인프라. 레버리지 ETF 없음(SMH/SOXX/QQQ는 1x).
EXPANDED_UNIVERSE = (
    "NVDA", "AMD", "AVGO", "ARM", "MU", "SMCI", "MRVL", "QCOM", "INTC", "TSM",
    "ASML", "AMAT", "LRCX", "KLAC", "TER", "ON", "MPWR", "MCHP", "TXN", "ADI",
    "NXPI", "WDC", "STX", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA",
    "ORCL", "IBM", "CRM", "NOW", "ADBE", "SNOW", "DDOG", "NET", "CRWD", "PANW",
    "ZS", "PLTR", "ANET", "DELL", "HPE", "VRT", "COHR", "CDNS", "SNPS",
    "TEAM", "MDB", "SHOP", "UBER", "APP", "SMH", "SOXX",
)


def _config_to_args(settings, symbols) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols), start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=_FILL_MODEL, entry_limit_buffer_pct=_BUFFER, weekend_exit_symbols=[],
    )


def _tradable(requested, available):
    """요청 유니버스 ∩ 가용 데이터 − 보조/레버리지. 거래 가능 심볼만."""
    avail = set(available)
    skip = set(_AUX) | LEVERAGED_ETFS
    return [s for s in requested if s in avail and s not in skip]


def _run_one(name, requested, settings, fn, *, available, bench_price_data, bench_universe):
    """한 유니버스 변형 재시뮬 + 요약. 거래 심볼 없으면 (None, None)."""
    present = _tradable(requested, available)
    if not present:
        return summarize_universe(name, requested, present, None, SimpleNamespace(symbol_perf=(), windows=()),
                                  SimpleNamespace(baselines=())), present
    args = _config_to_args(settings, present)
    res = fn(args)
    diag = compute_trade_diagnostics(res.multiday, final_prices=run_sim._final_marks(args, res))
    robustness = compute_robustness_report(res.multiday, {}, trade_diag=diag)
    bench = compute_baseline_comparison(
        res.performance, bench_price_data, universe=[s for s in present if s in bench_universe],
        benchmark_symbol=settings["benchmark"], qqq_symbol=settings.get("qqq_symbol", "QQQ"),
    )
    return summarize_universe(name, requested, present, res.performance, robustness, bench), present


def run_universe_bias(
    *, data_root, benchmark="SPY", qqq_symbol="QQQ", events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None,
):
    """4개 유니버스 변형을 잠긴 베이스라인으로 돌려 편향 리포트를 만든다. (report, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, qqq_symbol=qqq_symbol,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)

    probe = _config_to_args(settings, BASELINE_UNIVERSE)
    probe.symbols = None
    try:
        price_data, benchmark_prices = run_sim._feature_inputs(probe)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)
    available = set(price_data or {})

    bench_price_data = dict(price_data or {})
    if benchmark_prices is not None and benchmark not in bench_price_data:
        bench_price_data[benchmark] = pd.DataFrame({"close": benchmark_prices})
    bench_universe = [s for s in (price_data or {}) if s != benchmark and s != qqq_symbol]

    def _one(name, requested):
        return _run_one(name, requested, settings, fn, available=available,
                        bench_price_data=bench_price_data, bench_universe=bench_universe)

    base_result, _ = _one("baseline", BASELINE_UNIVERSE)
    expanded_result, _ = _one("expanded", EXPANDED_UNIVERSE)
    no_mu_result, _ = _one("expanded_no_mu", tuple(s for s in EXPANDED_UNIVERSE if s != "MU"))

    top3 = [s for s in base_result.top3_symbols] if base_result else []
    no_top3_universe = tuple(s for s in EXPANDED_UNIVERSE if s not in set(top3))
    no_top3_result, _ = _one("expanded_no_top3", no_top3_universe)

    report = build_universe_bias([base_result, expanded_result, no_mu_result, no_top3_result])
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="유니버스 확장/편향 테스트(실험 전용, 실주문 0)")
    p.add_argument("--data-root", default="data/ndu_export_expanded")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--qqq-symbol", default="QQQ")
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default="reports/universe_bias_test.md")
    return p


def run(args) -> int:
    report, error = run_universe_bias(
        data_root=args.data_root, benchmark=args.benchmark, qqq_symbol=args.qqq_symbol,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_universe_bias_markdown(report)
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
