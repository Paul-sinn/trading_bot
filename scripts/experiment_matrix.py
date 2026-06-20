"""실험 매트릭스 러너 — 여러 유니버스/설정을 기존 run_sim 로직으로 돌려 한 표로 비교한다.

새 매매 경로를 만들지 않는다. run_sim.simulate(데이터 로드→historical_sim→perf)와
compute_robustness_report를 호출만 해 메트릭을 모은다. 전략/스캐너/디시전/사이징/RiskGate 불변.

사용:
  python scripts/experiment_matrix.py --small-root data/ndu_export --expanded-root data/ndu_export_expanded \
      --events-csv data/events.csv

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

CRITICAL (fail-closed, per-experiment): 데이터 폴더/벤치마크/events.csv 누락·잘못된 설정은 run_sim이
DataAdapterError. 매트릭스는 이를 해당 실험 error로 담고 나머지는 계속(전체 크래시 금지, 가짜 메트릭 금지).

spec: specs/experiment_matrix.md
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import run_sim  # noqa: E402  (scripts/run_sim.py — simulate/_feature_inputs 재사용)
from agents.robustness_report import compute_robustness_report  # noqa: E402

# 표준 유니버스(벤치마크/컴퍼스 SPY 포함).
SMALL_UNIVERSE = ("SPY", "NVDA", "AAPL", "MSFT", "AMD", "GOOGL")
EXPANDED_UNIVERSE = (
    "SPY", "QQQ", "NVDA", "AMD", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA",
    "AVGO", "SMCI", "ARM", "MU", "TSM", "ASML", "NFLX", "ORCL", "CRM", "PLTR",
)


@dataclass(frozen=True)
class ExperimentConfig:
    """한 실험의 고정 설정(run_sim 플래그와 1:1)."""

    name: str
    data_root: str
    benchmark: str = "SPY"
    symbols: tuple[str, ...] | None = None
    warmup: int = 125
    starting_cash: float = 1000.0
    share_mode: str = "fractional"
    stop_loss_pct: float | None = 0.15
    trailing_stop_pct: float | None = 0.20
    max_holding_days: int | None = 60
    events_csv: str | None = "data/events.csv"
    assume_no_events: bool = False
    lot_size: float = 0.001
    start_date: str | None = None
    end_date: str | None = None


@dataclass(frozen=True)
class ExperimentResult:
    """한 실험의 핵심 메트릭(측정 보조 — 판단 아님). 실패 시 error만 채워진다."""

    name: str
    symbols_count: int
    trades: int
    cumulative_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    total_pnl: float | None
    top_symbol: str | None
    top_symbol_pnl_share: float | None
    robustness_warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class MatrixReport:
    """여러 실험 비교 묶음. real_orders_placed는 항상 0."""

    results: tuple[ExperimentResult, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _config_to_args(config: ExperimentConfig) -> SimpleNamespace:
    """ExperimentConfig → run_sim.simulate/_feature_inputs가 읽는 args 네임스페이스."""
    return SimpleNamespace(
        data_root=config.data_root,
        benchmark=config.benchmark,
        symbols=list(config.symbols) if config.symbols else None,
        start_date=config.start_date,
        end_date=config.end_date,
        warmup=config.warmup,
        starting_cash=config.starting_cash,
        share_mode=config.share_mode,
        lot_size=config.lot_size,
        stop_loss_pct=config.stop_loss_pct,
        trailing_stop_pct=config.trailing_stop_pct,
        max_holding_days=config.max_holding_days,
        manual_exit_date=None,
        events_csv=config.events_csv,
        assume_no_events=config.assume_no_events,
    )


def _default_simulate(config: ExperimentConfig):
    """run_sim.simulate로 실험을 돌리고 (result, robustness, symbols_count)을 돌려준다.

    데이터/설정 문제는 run_sim.DataAdapterError로 그대로 올린다(호출자가 fail-safe 처리).
    """
    args = _config_to_args(config)
    result = run_sim.simulate(args)
    price_data, _ = run_sim._feature_inputs(args)
    robustness = compute_robustness_report(result.multiday, price_data)
    count = len(config.symbols) if config.symbols else len(price_data)
    return result, robustness, count


def _error_result(config: ExperimentConfig, message: str) -> ExperimentResult:
    count = len(config.symbols) if config.symbols else 0
    return ExperimentResult(
        name=config.name, symbols_count=count, trades=0,
        cumulative_return=None, max_drawdown=None, win_rate=None, total_pnl=None,
        top_symbol=None, top_symbol_pnl_share=None, robustness_warnings=(),
        error=message,
    )


def run_experiment(config: ExperimentConfig, *, simulate_fn=None) -> ExperimentResult:
    """한 실험을 돌려 메트릭을 추출한다. 실패는 error로 담는다(가짜 메트릭 금지)."""
    fn = simulate_fn or _default_simulate
    try:
        result, robustness, count = fn(config)
    except run_sim.DataAdapterError as exc:
        return _error_result(config, str(exc))

    perf = result.performance
    return ExperimentResult(
        name=config.name,
        symbols_count=count,
        trades=perf.num_trades,
        cumulative_return=perf.cumulative_return,
        max_drawdown=perf.max_drawdown,
        win_rate=perf.win_rate,
        total_pnl=perf.total_pnl,
        top_symbol=robustness.top_symbol,
        top_symbol_pnl_share=robustness.top_symbol_pnl_share,
        robustness_warnings=tuple(robustness.warnings),
        error=None,
    )


def run_matrix(configs, *, simulate_fn=None) -> MatrixReport:
    """실험들을 순서대로 돌린다. 한 실험 실패가 나머지를 막지 않는다(fail-safe)."""
    results = [run_experiment(c, simulate_fn=simulate_fn) for c in configs]
    return MatrixReport(results=tuple(results))


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_matrix(report: MatrixReport) -> str:
    """실험 비교표(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 86)
    lines.append("Experiment Matrix (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 86)
    lines.append(
        f"  {'experiment':<12}{'syms':>5}{'trades':>7}{'cum_ret':>9}{'MDD':>8}"
        f"{'win':>7}{'total_PnL':>11}{'top_symbol(share)':>20}{'warn':>5}{'orders':>7}"
    )
    for r in report.results:
        if r.error is not None:
            lines.append(f"  {r.name:<12}{r.symbols_count:>5}  [FAILED] {r.error}")
            continue
        top = f"{r.top_symbol or '-'}({_fmt(r.top_symbol_pnl_share, '{:.0%}')})"
        lines.append(
            f"  {r.name:<12}{r.symbols_count:>5}{r.trades:>7}{_fmt(r.cumulative_return):>9}"
            f"{_fmt(r.max_drawdown):>8}{_fmt(r.win_rate):>7}{_fmt(r.total_pnl, '{:.2f}'):>11}"
            f"{top:>20}{len(r.robustness_warnings):>5}{r.real_orders_placed:>7}"
        )

    # 경고 상세(있을 때만).
    for r in report.results:
        if r.robustness_warnings:
            lines.append(f"  [{r.name}] robustness warnings:")
            for w in r.robustness_warnings:
                lines.append(f"    ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 86)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="실험 매트릭스: small/expanded 유니버스를 같은 설정으로 비교(실주문 0)"
    )
    p.add_argument("--small-root", default=None, help="small 유니버스 데이터 폴더(예: data/ndu_export)")
    p.add_argument("--expanded-root", default=None, help="expanded 유니버스 데이터 폴더")
    p.add_argument("--benchmark", default="SPY", help="벤치마크 심볼(SPY 기본, QQQ 등)")
    p.add_argument("--warmup", type=int, default=125)
    p.add_argument("--events-csv", default="data/events.csv", help="이벤트 캘린더 CSV")
    p.add_argument(
        "--assume-no-events", action="store_true",
        help="개발 바이패스 전용: 이벤트 없음 가정(실 캘린더 아님).",
    )
    p.add_argument("--output", default=None, help="비교표 저장 경로(UTF-8). 콘솔에도 항상 출력.")
    return p


def _build_configs(args) -> list[ExperimentConfig]:
    """CLI args → 표준 small/expanded 실험 설정(루트가 주어진 것만)."""
    events_csv = None if args.assume_no_events else args.events_csv
    common = dict(
        benchmark=args.benchmark, warmup=args.warmup,
        events_csv=events_csv, assume_no_events=args.assume_no_events,
    )
    configs: list[ExperimentConfig] = []
    if args.small_root:
        configs.append(ExperimentConfig(
            name="small", data_root=args.small_root, symbols=SMALL_UNIVERSE, **common,
        ))
    if args.expanded_root:
        configs.append(ExperimentConfig(
            name="expanded", data_root=args.expanded_root, symbols=EXPANDED_UNIVERSE, **common,
        ))
    return configs


def run(args) -> int:
    configs = _build_configs(args)
    if not configs:
        print("[설정 오류] --small-root 또는 --expanded-root 중 최소 하나를 지정하라.", file=sys.stderr)
        raise SystemExit(2)

    report = run_matrix(configs)
    text = format_matrix(report)
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
