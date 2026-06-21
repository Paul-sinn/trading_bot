"""실행 정책 로버스트니스 검증 러너 — next-open vs 3% limit을 시간창/LOO/슬리피지로 검증한다.

60일 베이스라인을 고정하고 entry_fill_model만 바꿔 run_sim.simulate를 돌린다. LOO는 심볼을 하나씩 빼고
next-open 재시뮬. 갭 가드 미적용. 스캐너/디시전/사이징/RiskGate 변경 없음. winner extension 미적용.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/execution_robustness.md
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
from agents.execution_robustness import (  # noqa: E402
    PolicySummary,
    build_validation,
    compute_leave_one_out,
    compute_slippage_robustness,
    compute_window_comparison,
    format_robustness_validation,
)
from agents.robustness_report import compute_robustness_report  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402

_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"
_SLIPPAGES = (0.0, 0.0025, 0.005, 0.01)


def _config_to_args(settings, model, symbols) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(symbols) if symbols else None,
        start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=model, entry_limit_buffer_pct=0.03, weekend_exit_symbols=[],
    )


def _summary(perf) -> PolicySummary:
    return PolicySummary(cumulative_return=perf.cumulative_return, max_drawdown=perf.max_drawdown,
                         win_rate=perf.win_rate, total_pnl=perf.total_pnl, trades=perf.num_trades)


def run_execution_robustness(
    *, data_root, benchmark="SPY", symbols=None, events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, do_loo=True, simulate_fn=None,
):
    """두 정책 풀런 + LOO 재시뮬로 검증을 만든다. 데이터 실패는 (None, error)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, symbols=symbols,
                    events_csv=events_csv, assume_no_events=assume_no_events, starting_cash=starting_cash)
    limit_args = _config_to_args(settings, "next-bar-limit", symbols)
    nopen_args = _config_to_args(settings, "next-open", symbols)
    try:
        limit_res = fn(limit_args)
        nopen_res = fn(nopen_args)
        price_data, _ = run_sim._feature_inputs(limit_args)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)

    limit_diag = compute_trade_diagnostics(limit_res.multiday, final_prices=run_sim._final_marks(limit_args, limit_res))
    nopen_diag = compute_trade_diagnostics(nopen_res.multiday, final_prices=run_sim._final_marks(nopen_args, nopen_res))
    limit_rob = compute_robustness_report(limit_res.multiday, price_data, trade_diag=limit_diag)
    nopen_rob = compute_robustness_report(nopen_res.multiday, price_data, trade_diag=nopen_diag)

    windows = compute_window_comparison(limit_rob.windows, nopen_rob.windows)
    slippage = compute_slippage_robustness(limit_diag, nopen_diag, slippages=_SLIPPAGES, starting_cash=starting_cash)
    nopen_symbol_pnl = {s.symbol: s.total_pnl for s in nopen_rob.symbol_perf}

    loo_pnl: dict[str, float] = {}
    if do_loo:
        universe = list(price_data)
        for s in universe:
            drop_syms = [x for x in universe if x != s]
            if not drop_syms:
                continue
            try:
                r = fn(_config_to_args(settings, "next-open", drop_syms))
            except run_sim.DataAdapterError:
                continue
            loo_pnl[s] = r.performance.total_pnl

    loo = compute_leave_one_out(nopen_res.performance.total_pnl, loo_pnl)
    report = build_validation(_summary(limit_res.performance), _summary(nopen_res.performance),
                              windows, loo, slippage, nopen_symbol_pnl)
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="실행 정책 로버스트니스 검증(next-open vs 3% limit, 실주문 0)")
    p.add_argument("--data-root", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--no-loo", action="store_true", help="leave-one-symbol-out 재시뮬 생략")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report, error = run_execution_robustness(
        data_root=args.data_root, benchmark=args.benchmark, symbols=args.symbols,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
        do_loo=not args.no_loo,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_robustness_validation(report)
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
