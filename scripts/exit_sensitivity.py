"""청산 정책 민감도 매트릭스 — stop×trailing×max-holding 그리드를 기존 run_sim 로직으로 스윕한다.

성과가 특정 청산 설정에 과도하게 의존하는지 본다. 청산 플래그만 바꿔 run_sim.simulate를 호출할 뿐,
전략/스캐너/디시전/사이징/RiskGate를 바꾸지 않고 어떤 새 규칙도 실 트레이드에 적용하지 않는다.

사용:
  python scripts/exit_sensitivity.py --data-root data/ndu_export_expanded --events-csv data/events.csv

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

spec: specs/exit_sensitivity.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from itertools import product
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
from agents.robustness_report import compute_robustness_report  # noqa: E402

# 고정 그리드(최적화 아님).
STOP_GRID = (0.10, 0.15, 0.20)
TRAIL_GRID = (0.15, 0.20, 0.25)
HOLD_GRID = (45, 60, 90)
DEFAULT_COMBO = (0.15, 0.20, 60)

_NEAR_BEST = 0.8       # 최고수익의 80%+면 "근접".
_SPREAD_WARN = 0.5     # 상대 스프레드 > 0.5면 민감/붕괴.


@dataclass(frozen=True)
class ExitGridConfig:
    """그리드 스윕 공통 설정(청산 파라미터는 그리드가 채움)."""

    data_root: str
    benchmark: str = "SPY"
    symbols: tuple[str, ...] | None = None
    warmup: int = 125
    starting_cash: float = 1000.0
    share_mode: str = "fractional"
    events_csv: str | None = "data/events.csv"
    assume_no_events: bool = False
    lot_size: float = 0.001
    start_date: str | None = None
    end_date: str | None = None
    stop_grid: tuple[float, ...] = STOP_GRID
    trail_grid: tuple[float, ...] = TRAIL_GRID
    hold_grid: tuple[int, ...] = HOLD_GRID


@dataclass(frozen=True)
class ExitRunResult:
    """한 청산 조합의 성과(측정 보조). 실패 시 error만 채워진다."""

    stop_loss_pct: float
    trailing_stop_pct: float
    max_holding_days: int
    cumulative_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    total_pnl: float | None
    trades: int
    return_mdd_ratio: float | None
    robustness_warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class ExitSensitivityReport:
    """청산 민감도 매트릭스 묶음. real_orders_placed는 항상 0."""

    results: tuple[ExitRunResult, ...]
    best_by_return: ExitRunResult | None
    best_by_return_mdd: ExitRunResult | None
    safest_by_mdd: ExitRunResult | None
    default_result: ExitRunResult | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def generate_grid(stop_grid, trail_grid, hold_grid) -> list[tuple]:
    """stop×trail×hold 곱집합(결정론적 순서)."""
    return [(s, t, h) for s, t, h in product(stop_grid, trail_grid, hold_grid)]


def _config_to_args(config: ExitGridConfig, stop, trail, hold) -> SimpleNamespace:
    """설정 + 청산 조합 → run_sim.simulate가 읽는 args."""
    return SimpleNamespace(
        data_root=config.data_root, benchmark=config.benchmark,
        symbols=list(config.symbols) if config.symbols else None,
        start_date=config.start_date, end_date=config.end_date, warmup=config.warmup,
        starting_cash=config.starting_cash, share_mode=config.share_mode, lot_size=config.lot_size,
        stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=hold,
        manual_exit_date=None, events_csv=config.events_csv, assume_no_events=config.assume_no_events,
    )


def _default_simulate(config: ExitGridConfig, stop, trail, hold):
    """run_sim.simulate로 한 조합을 돌려 (result, robustness)을 돌려준다."""
    args = _config_to_args(config, stop, trail, hold)
    result = run_sim.simulate(args)
    price_data, _ = run_sim._feature_inputs(args)
    robustness = compute_robustness_report(result.multiday, price_data)
    return result, robustness


def _ratio(cum, mdd) -> float | None:
    if cum is None or mdd is None or mdd <= 0:
        return None
    return cum / mdd


def run_one(config: ExitGridConfig, stop, trail, hold, *, simulate_fn=None) -> ExitRunResult:
    """한 청산 조합을 돌린다. 실패는 error로 담는다(가짜 메트릭 금지)."""
    fn = simulate_fn or _default_simulate
    try:
        result, robustness = fn(config, stop, trail, hold)
    except run_sim.DataAdapterError as exc:
        return ExitRunResult(
            stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=hold,
            cumulative_return=None, max_drawdown=None, win_rate=None, total_pnl=None,
            trades=0, return_mdd_ratio=None, robustness_warnings=(), error=str(exc),
        )
    perf = result.performance
    return ExitRunResult(
        stop_loss_pct=stop, trailing_stop_pct=trail, max_holding_days=hold,
        cumulative_return=perf.cumulative_return, max_drawdown=perf.max_drawdown,
        win_rate=perf.win_rate, total_pnl=perf.total_pnl, trades=perf.num_trades,
        return_mdd_ratio=_ratio(perf.cumulative_return, perf.max_drawdown),
        robustness_warnings=tuple(robustness.warnings), error=None,
    )


def _fragility_warnings(ok) -> list[str]:
    """성공 조합들의 수익 분포로 단일의존/민감붕괴 경고를 만든다."""
    warnings: list[str] = []
    returns = [r.cumulative_return for r in ok]
    best = max(returns)
    if best > 0:
        near = sum(1 for r in returns if r >= _NEAR_BEST * best)
        if near <= 1:
            warnings.append("최고 성과가 단일(좁은) 청산 설정에만 집중 — 과적합 위험")
        rel_spread = (best - min(returns)) / best
        if rel_spread > _SPREAD_WARN:
            warnings.append(
                f"청산 파라미터 민감도 높음(수익 상대 스프레드 {rel_spread:.0%}) — 작은 변경에 성과 크게 변동"
            )
    if any(r < 0 for r in returns):
        neg = sum(1 for r in returns if r < 0)
        warnings.append(f"{neg}개 조합에서 성과 붕괴(음수 수익) — 일부 청산 설정은 손실")
    return warnings


def run_sensitivity(config: ExitGridConfig, *, simulate_fn=None) -> ExitSensitivityReport:
    """그리드 전체를 스윕해 best/safest와 취약성 경고를 산출한다(fail-safe)."""
    grid = generate_grid(config.stop_grid, config.trail_grid, config.hold_grid)
    results = [run_one(config, s, t, h, simulate_fn=simulate_fn) for s, t, h in grid]

    ok = [r for r in results if r.error is None and r.cumulative_return is not None]
    default_result = next(
        (r for r in results
         if (r.stop_loss_pct, r.trailing_stop_pct, r.max_holding_days) == DEFAULT_COMBO),
        None,
    )

    if not ok:
        first_err = next((r.error for r in results if r.error), "알 수 없음")
        return ExitSensitivityReport(
            results=tuple(results), best_by_return=None, best_by_return_mdd=None,
            safest_by_mdd=None, default_result=default_result,
            warnings=(f"모든 청산 조합 실패(데이터 문제?): {first_err}",),
        )

    best_by_return = max(ok, key=lambda r: r.cumulative_return)
    safest_by_mdd = min(ok, key=lambda r: r.max_drawdown)
    rated = [r for r in ok if r.return_mdd_ratio is not None]
    best_by_return_mdd = max(rated, key=lambda r: r.return_mdd_ratio) if rated else None

    return ExitSensitivityReport(
        results=tuple(results),
        best_by_return=best_by_return,
        best_by_return_mdd=best_by_return_mdd,
        safest_by_mdd=safest_by_mdd,
        default_result=default_result,
        warnings=tuple(_fragility_warnings(ok)),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _combo(r) -> str:
    return f"{r.stop_loss_pct:.2f}/{r.trailing_stop_pct:.2f}/{r.max_holding_days}"


def format_exit_sensitivity(report: ExitSensitivityReport) -> str:
    """청산 민감도 비교표(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 84)
    lines.append("Exit Policy Sensitivity (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 84)
    lines.append("grid: stop_loss / trailing_stop / max_holding_days")
    lines.append(
        f"  {'stop/trail/hold':<18}{'cum_ret':>9}{'MDD':>8}{'win':>7}"
        f"{'total_PnL':>11}{'trades':>7}{'ret/MDD':>9}{'warn':>5}"
    )
    for r in report.results:
        mark = "  <- default" if (r.stop_loss_pct, r.trailing_stop_pct, r.max_holding_days) == DEFAULT_COMBO else ""
        if r.error is not None:
            lines.append(f"  {_combo(r):<18}  [FAILED] {r.error}{mark}")
            continue
        lines.append(
            f"  {_combo(r):<18}{_fmt(r.cumulative_return):>9}{_fmt(r.max_drawdown):>8}"
            f"{_fmt(r.win_rate):>7}{_fmt(r.total_pnl, '{:.2f}'):>11}{r.trades:>7}"
            f"{_fmt(r.return_mdd_ratio, '{:.2f}'):>9}{len(r.robustness_warnings):>5}{mark}"
        )

    def _line(label, r):
        if r is None:
            return f"{label}: n/a"
        return f"{label}: {_combo(r)}  ret {_fmt(r.cumulative_return)}  MDD {_fmt(r.max_drawdown)}  ret/MDD {_fmt(r.return_mdd_ratio, '{:.2f}')}"

    lines.append(_line("best by return    ", report.best_by_return))
    lines.append(_line("best by return/MDD", report.best_by_return_mdd))
    lines.append(_line("safest (low MDD)  ", report.safest_by_mdd))
    lines.append(_line("default setting   ", report.default_result))

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 84)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="청산 정책 민감도 매트릭스(stop×trail×hold 스윕, 실주문 0)"
    )
    p.add_argument("--data-root", required=True, help="데이터 폴더(예: data/ndu_export_expanded)")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--warmup", type=int, default=125)
    p.add_argument("--starting-cash", type=float, default=1000.0)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--output", default=None, help="비교표 저장 경로(UTF-8). 콘솔에도 항상 출력.")
    return p


def run(args) -> int:
    config = ExitGridConfig(
        data_root=args.data_root, benchmark=args.benchmark,
        symbols=tuple(args.symbols) if args.symbols else None, warmup=args.warmup,
        starting_cash=args.starting_cash,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events,
    )
    report = run_sensitivity(config)
    text = format_exit_sensitivity(report)
    print(text)
    if args.output:
        out = Path(args.output)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"비교표 저장: {out}")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
