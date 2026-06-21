"""진입 실행 매트릭스 — 현실적 진입 정책(current / next-bar-limit 1·2·3% / next-open)을 실제 시뮬로 비교.

60일 베이스라인을 그대로 두고 entry_fill_model/buffer만 바꿔 run_sim.simulate를 호출만 한다. 새 매매
경로 없음. winner extension 미적용. 레버리지 주말청산은 비움(일반주 미적용). 스캐너/디시전/사이징/
RiskGate 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

spec: specs/entry_execution_matrix.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import run_sim  # noqa: E402
from agents.trade_diagnostics import compute_trade_diagnostics  # noqa: E402

# 고정 베이스라인(잠금).
_STOP = 0.15
_TRAIL = 0.20
_MAX_HOLD = 60
_SHARE_MODE = "fractional"


@dataclass(frozen=True)
class PolicyResult:
    """한 진입 실행 정책의 메트릭(측정 보조). 실패 시 error만 채워진다."""

    name: str
    entry_fill_model: str
    buffer: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    total_pnl: float | None
    trades: int
    avg_holding_days: float | None
    return_mdd_ratio: float | None
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    exit_reason_dist: tuple[tuple[str, int], ...]
    weekend_exit_count: int
    error: str | None = None

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class ExecutionMatrixReport:
    """진입 실행 정책 비교 묶음. real_orders_placed는 항상 0."""

    policies: tuple[PolicyResult, ...]
    best_by_return_mdd: PolicyResult | None
    best_by_return: PolicyResult | None

    @property
    def real_orders_placed(self) -> int:
        return 0


def generate_policies():
    """고정 비교 정책 (name, entry_fill_model, buffer)."""
    return (
        ("current", "current", 0.03),                 # buffer는 current에 미사용.
        ("next-bar-limit-1%", "next-bar-limit", 0.01),
        ("next-bar-limit-2%", "next-bar-limit", 0.02),
        ("next-bar-limit-3%", "next-bar-limit", 0.03),
        ("next-open", "next-open", 0.03),
    )


def _config_to_args(settings, model, buffer) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=settings["data_root"], benchmark=settings["benchmark"],
        symbols=list(settings["symbols"]) if settings.get("symbols") else None,
        start_date=None, end_date=None, warmup=settings.get("warmup", 125),
        starting_cash=settings.get("starting_cash", 1000.0), share_mode=_SHARE_MODE, lot_size=0.001,
        stop_loss_pct=_STOP, trailing_stop_pct=_TRAIL, max_holding_days=_MAX_HOLD, manual_exit_date=None,
        events_csv=settings.get("events_csv"), assume_no_events=settings.get("assume_no_events", False),
        entry_fill_model=model, entry_limit_buffer_pct=buffer,
        weekend_exit_symbols=[],          # 일반주 — 주말청산 미적용(레버리지 전용 기능 비움).
    )


def _holding_days(leg, last_date) -> int | None:
    if leg.entry_date is None:
        return None
    try:
        entry = pd.Timestamp(leg.entry_date)
        end = pd.Timestamp(leg.exit_date) if leg.exit_date else (pd.Timestamp(last_date) if last_date else None)
    except (ValueError, TypeError):
        return None
    return int((end - entry).days) if end is not None else None


def _policy_metrics(name, model, buffer, performance, legs, last_date, *, error=None) -> PolicyResult:
    if error is not None or performance is None:
        return PolicyResult(
            name=name, entry_fill_model=model, buffer=buffer, cumulative_return=None,
            max_drawdown=None, win_rate=None, total_pnl=None, trades=0, avg_holding_days=None,
            return_mdd_ratio=None, top_symbol=None, top_symbol_pnl_share=None,
            exit_reason_dist=(), weekend_exit_count=0, error=error,
        )
    holds = [h for h in (_holding_days(l, last_date) for l in legs) if h is not None]
    closed = [l for l in legs if l.exit_date is not None]
    dist = Counter(l.exit_reason for l in closed if l.exit_reason)

    pnl_by: dict[str, float] = {}
    for l in legs:
        if l.pnl is not None:
            pnl_by[l.symbol] = pnl_by.get(l.symbol, 0.0) + l.pnl
    pos_total = sum(v for v in pnl_by.values() if v > 0)
    top_sym = max(pnl_by, key=lambda s: pnl_by[s]) if pnl_by else None
    share = (pnl_by[top_sym] / pos_total) if (top_sym and pos_total > 0 and pnl_by[top_sym] > 0) else None
    ratio = (performance.cumulative_return / performance.max_drawdown) if performance.max_drawdown > 0 else None

    return PolicyResult(
        name=name, entry_fill_model=model, buffer=buffer,
        cumulative_return=performance.cumulative_return, max_drawdown=performance.max_drawdown,
        win_rate=performance.win_rate, total_pnl=performance.total_pnl, trades=performance.num_trades,
        avg_holding_days=(statistics.fmean(holds) if holds else None), return_mdd_ratio=ratio,
        top_symbol=top_sym, top_symbol_pnl_share=share,
        exit_reason_dist=tuple(dist.most_common()), weekend_exit_count=dist.get("weekend_exit", 0),
        error=None,
    )


def run_policy(settings, name, model, buffer, *, simulate_fn=None) -> PolicyResult:
    """한 진입 정책을 돌려 메트릭을 추출한다. 데이터 실패는 error로 담는다(가짜 메트릭 금지)."""
    fn = simulate_fn or run_sim.simulate
    args = _config_to_args(settings, model, buffer)
    try:
        result = fn(args)
    except run_sim.DataAdapterError as exc:
        return _policy_metrics(name, model, buffer, None, [], None, error=str(exc))
    diag = compute_trade_diagnostics(result.multiday, final_prices=run_sim._final_marks(args, result))
    last_date = diag.equity_over_time[-1][0] if diag.equity_over_time else None
    return _policy_metrics(name, model, buffer, result.performance, list(diag.trades), last_date)


def compute_entry_execution_matrix(
    *, data_root, benchmark="SPY", symbols=None, events_csv="data/events.csv",
    assume_no_events=False, simulate_fn=None,
) -> ExecutionMatrixReport:
    """진입 실행 정책들을 같은 60일 베이스라인으로 비교한다(읽기 전용 — 입력 불변)."""
    settings = dict(data_root=data_root, benchmark=benchmark, symbols=symbols,
                    events_csv=events_csv, assume_no_events=assume_no_events)
    policies = tuple(
        run_policy(settings, name, model, buffer, simulate_fn=simulate_fn)
        for name, model, buffer in generate_policies()
    )
    rated = [p for p in policies if p.return_mdd_ratio is not None]
    ok = [p for p in policies if p.cumulative_return is not None]
    best_ratio = max(rated, key=lambda p: p.return_mdd_ratio) if rated else None
    best_ret = max(ok, key=lambda p: p.cumulative_return) if ok else None
    return ExecutionMatrixReport(policies=policies, best_by_return_mdd=best_ratio, best_by_return=best_ret)


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_entry_execution_matrix(report: ExecutionMatrixReport) -> str:
    """진입 실행 비교표(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 96]
    lines.append("Entry Execution Matrix (측정 - 실주문 없음, 60일 베이스라인 잠금)")
    lines.append("=" * 96)
    lines.append(
        f"  {'policy':<18}{'cum_ret':>9}{'MDD':>8}{'win':>7}{'total_PnL':>11}{'trades':>7}"
        f"{'avgHld':>7}{'ret/MDD':>8}{'topShare':>9}{'wkEx':>6}"
    )
    for p in report.policies:
        if p.error is not None:
            lines.append(f"  {p.name:<18}  [FAILED] {p.error}")
            continue
        lines.append(
            f"  {p.name:<18}{_fmt(p.cumulative_return):>9}{_fmt(p.max_drawdown):>8}{_fmt(p.win_rate):>7}"
            f"{_fmt(p.total_pnl, '{:.2f}'):>11}{p.trades:>7}{_fmt(p.avg_holding_days, '{:.0f}'):>7}"
            f"{_fmt(p.return_mdd_ratio, '{:.2f}'):>8}{_fmt(p.top_symbol_pnl_share, '{:.0%}'):>9}"
            f"{p.weekend_exit_count:>6}"
        )
    for p in report.policies:
        if p.error is None and p.exit_reason_dist:
            dist = ", ".join(f"{r}={n}" for r, n in p.exit_reason_dist)
            lines.append(f"  [{p.name}] exits: {dist}  top {p.top_symbol}")

    if report.best_by_return_mdd is not None:
        b = report.best_by_return_mdd
        lines.append(f"best return/MDD : {b.name} (ret/MDD {_fmt(b.return_mdd_ratio, '{:.2f}')}, "
                     f"cum {_fmt(b.cumulative_return)}, MDD {_fmt(b.max_drawdown)})")
    if report.best_by_return is not None:
        lines.append(f"best return     : {report.best_by_return.name} ({_fmt(report.best_by_return.cumulative_return)})")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 96)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="진입 실행 매트릭스(current/next-bar-limit/next-open, 실주문 0)")
    p.add_argument("--data-root", required=True)
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report = compute_entry_execution_matrix(
        data_root=args.data_root, benchmark=args.benchmark, symbols=args.symbols,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events,
    )
    text = format_entry_execution_matrix(report)
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
