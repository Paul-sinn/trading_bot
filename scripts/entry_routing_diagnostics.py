"""진입 실행 라우팅 진단 러너 — 3% limit + next-open 두 시뮬을 돌려 심볼 갭 라우팅 what-if을 만든다.

60일 베이스라인을 고정하고 entry_fill_model만 바꿔 run_sim.simulate를 두 번 호출한다(리포트 전용).
스캐너/디시전/사이징/RiskGate 변경 없음. winner extension 미적용. weekend_exit_symbols 비움(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

spec: specs/entry_routing.md
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
from agents.entry_routing import compute_entry_routing, format_entry_routing  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402

# 고정 베이스라인(잠금).
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


def _config_to_args(settings, model, buffer) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(settings["symbols"]) if settings.get("symbols") else None,
        start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=model, entry_limit_buffer_pct=buffer,
        weekend_exit_symbols=[],          # 일반주 — 주말청산 미적용.
    )


def run_routing_diagnostics(
    *, data_root, benchmark="SPY", symbols=None, events_csv="data/events.csv",
    assume_no_events=False, starting_cash=1000.0, simulate_fn=None,
):
    """3% limit + next-open 시뮬 두 번 → compute_entry_routing. 데이터 실패는 None(호출부 처리)."""
    fn = simulate_fn or run_sim.simulate
    settings = dict(data_root=data_root, benchmark=benchmark, symbols=symbols,
                    events_csv=events_csv, assume_no_events=assume_no_events,
                    starting_cash=starting_cash)
    limit_args = _config_to_args(settings, "next-bar-limit", 0.03)
    nopen_args = _config_to_args(settings, "next-open", 0.03)
    try:
        limit_res = fn(limit_args)
        nopen_res = fn(nopen_args)
        price_data, _ = run_sim._feature_inputs(limit_args)
    except run_sim.DataAdapterError as exc:
        return None, str(exc)

    limit_diag = compute_trade_diagnostics(
        limit_res.multiday, final_prices=run_sim._final_marks(limit_args, limit_res))
    nopen_diag = compute_trade_diagnostics(
        nopen_res.multiday, final_prices=run_sim._final_marks(nopen_args, nopen_res))
    report = compute_entry_routing(limit_diag, nopen_diag, price_data, starting_cash=starting_cash)
    return report, None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="진입 실행 라우팅 진단(3% limit vs next-open, 실주문 0)")
    p.add_argument("--data-root", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report, error = run_routing_diagnostics(
        data_root=args.data_root, benchmark=args.benchmark, symbols=args.symbols,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, starting_cash=args.starting_cash,
    )
    if report is None:
        print(f"[데이터 오류] {error}", file=sys.stderr)
        raise SystemExit(2)
    text = format_entry_routing(report)
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
