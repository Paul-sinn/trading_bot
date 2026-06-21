"""추세 연장 + 레버리지 ETF 주말리스크 실험 러너 — 기존 run_sim 로직으로 변형들을 비교한다.

일반 모멘텀 승자의 보유 연장(60/90/120)과 레버리지 ETF의 엄격 주말청산을 점검한다. 새 매매 경로를
만들지 않고 run_sim.simulate를 호출만 한다. 레버리지 주말청산만 opt-in sim 기능(레버리지 심볼 전용,
기본 불변). 전략/스캐너/디시전/사이징/RiskGate 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

spec: specs/trend_leverage_experiment.md
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
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

LEVERAGED_ETFS = ("TQQQ", "SQQQ", "SOXL", "UPRO", "SPXL", "SPXS", "TECL", "FNGU")
LEVERAGED_SHADOW_UNIVERSE = ("TQQQ", "SOXL", "UPRO", "SQQQ")
HEALTHY_TREND_CONDITIONS = (
    "price_above_50ma", "relative_strength>0", "trailing_stop_not_hit", "regime_not_risk_off",
)


@dataclass(frozen=True)
class VariantConfig:
    """한 변형의 설정(베이스라인 = next-bar-limit 0.03, stop15/trail20)."""

    name: str
    data_root: str
    symbols: tuple[str, ...] | None = None
    benchmark: str = "SPY"
    warmup: int = 125
    starting_cash: float = 1000.0
    share_mode: str = "fractional"
    stop_loss_pct: float | None = 0.15
    trailing_stop_pct: float | None = 0.20
    max_holding_days: int | None = 60
    entry_fill_model: str = "next-bar-limit"
    entry_limit_buffer_pct: float = 0.03
    events_csv: str | None = "data/events.csv"
    assume_no_events: bool = False
    weekend_exit_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class VariantResult:
    """한 변형의 메트릭(측정 보조). 실패 시 error만 채워진다."""

    name: str
    cumulative_return: float | None
    max_drawdown: float | None
    win_rate: float | None
    total_pnl: float | None
    trades: int
    avg_holding_days: float | None
    longest_holding_days: int | None
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
class ExtensionReport:
    """winner_extension — report-only(수익 time_stop 연장 후보 + 90/120 델타)."""

    profitable_time_stop_count: int
    profitable_time_stop_pnl: float
    losing_time_stop_count: int
    delta_total_pnl_90: float | None
    delta_total_pnl_120: float | None
    healthy_conditions: tuple[str, ...] = HEALTHY_TREND_CONDITIONS
    note: str = "report-only: 보유 중 동적 연장은 미배선(손실 포지션엔 미적용)"

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class ExperimentReport:
    """추세 연장 + 레버리지 실험 묶음. real_orders_placed는 항상 0."""

    variants: tuple[VariantResult, ...]
    extension: ExtensionReport | None
    leveraged: VariantResult | None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _config_to_args(config: VariantConfig) -> SimpleNamespace:
    return SimpleNamespace(
        data_root=config.data_root, benchmark=config.benchmark,
        symbols=list(config.symbols) if config.symbols else None,
        start_date=None, end_date=None, warmup=config.warmup,
        starting_cash=config.starting_cash, share_mode=config.share_mode, lot_size=0.001,
        stop_loss_pct=config.stop_loss_pct, trailing_stop_pct=config.trailing_stop_pct,
        max_holding_days=config.max_holding_days, manual_exit_date=None,
        events_csv=config.events_csv, assume_no_events=config.assume_no_events,
        entry_fill_model=config.entry_fill_model, entry_limit_buffer_pct=config.entry_limit_buffer_pct,
        weekend_exit_symbols=list(config.weekend_exit_symbols),
    )


def _holding_days(leg, last_date) -> int | None:
    if leg.entry_date is None:
        return None
    try:
        entry = pd.Timestamp(leg.entry_date)
        end = pd.Timestamp(leg.exit_date) if leg.exit_date else (pd.Timestamp(last_date) if last_date else None)
    except (ValueError, TypeError):
        return None
    if end is None:
        return None
    return int((end - entry).days)


def _variant_metrics(name, performance, legs, last_date, *, error=None) -> VariantResult:
    """성과 + 트레이드 leg에서 변형 메트릭을 만든다(순수)."""
    if error is not None or performance is None:
        return VariantResult(
            name=name, cumulative_return=None, max_drawdown=None, win_rate=None,
            total_pnl=None, trades=0, avg_holding_days=None, longest_holding_days=None,
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

    return VariantResult(
        name=name, cumulative_return=performance.cumulative_return,
        max_drawdown=performance.max_drawdown, win_rate=performance.win_rate,
        total_pnl=performance.total_pnl, trades=performance.num_trades,
        avg_holding_days=(statistics.fmean(holds) if holds else None),
        longest_holding_days=(max(holds) if holds else None),
        return_mdd_ratio=ratio, top_symbol=top_sym, top_symbol_pnl_share=share,
        exit_reason_dist=tuple(dist.most_common()),
        weekend_exit_count=dist.get("weekend_exit", 0), error=None,
    )


def compute_extension_candidates(legs):
    """winner_extension 후보 = 수익 time_stop 청산만(손실 포지션 제외)."""
    return tuple(l for l in legs if l.exit_reason == "time_stop" and l.pnl is not None and l.pnl > 0)


def _run_variant_full(config: VariantConfig, *, simulate_fn=None):
    """변형을 돌려 (VariantResult, legs, last_date)를 돌려준다. 데이터 실패는 error로 담는다."""
    fn = simulate_fn or run_sim.simulate
    args = _config_to_args(config)
    try:
        result = fn(args)
    except run_sim.DataAdapterError as exc:
        return _variant_metrics(config.name, None, [], None, error=str(exc)), [], None
    final_marks = run_sim._final_marks(args, result)
    diag = compute_trade_diagnostics(result.multiday, final_prices=final_marks)
    last_date = diag.equity_over_time[-1][0] if diag.equity_over_time else None
    legs = list(diag.trades)
    return _variant_metrics(config.name, result.performance, legs, last_date), legs, last_date


def run_variant(config: VariantConfig, *, simulate_fn=None) -> VariantResult:
    return _run_variant_full(config, simulate_fn=simulate_fn)[0]


def run_trend_leverage_experiment(
    *,
    universe_root: str,
    benchmark: str = "SPY",
    symbols=None,
    events_csv: str | None = "data/events.csv",
    assume_no_events: bool = False,
    leveraged_root: str | None = None,
    simulate_fn=None,
) -> ExperimentReport:
    """변형들을 돌려 비교한다(읽기 전용 — 입력 불변, 기본 동작 불변)."""
    base = dict(
        data_root=universe_root, benchmark=benchmark,
        symbols=tuple(symbols) if symbols else None,
        events_csv=events_csv, assume_no_events=assume_no_events,
    )
    warnings: list[str] = []

    baseline_res, baseline_legs, _ = _run_variant_full(
        VariantConfig(name="baseline_realistic", max_holding_days=60, **base), simulate_fn=simulate_fn)
    ext90 = run_variant(VariantConfig(name="trend_extended_90", max_holding_days=90, **base), simulate_fn=simulate_fn)
    ext120 = run_variant(VariantConfig(name="trend_extended_120", max_holding_days=120, **base), simulate_fn=simulate_fn)

    # winner_extension (report-only).
    cands = compute_extension_candidates(baseline_legs)
    losing_ts = sum(1 for l in baseline_legs if l.exit_reason == "time_stop" and l.pnl is not None and l.pnl <= 0)

    def _delta(v):
        if baseline_res.total_pnl is None or v.total_pnl is None:
            return None
        return v.total_pnl - baseline_res.total_pnl

    extension = ExtensionReport(
        profitable_time_stop_count=len(cands),
        profitable_time_stop_pnl=float(sum(l.pnl for l in cands)),
        losing_time_stop_count=losing_ts,
        delta_total_pnl_90=_delta(ext90), delta_total_pnl_120=_delta(ext120),
    )

    # leveraged_weekend_risk_shadow (별도 유니버스, 데이터 없으면 skip + 경고).
    leveraged = None
    if leveraged_root:
        lev_cfg = VariantConfig(
            name="leveraged_weekend_risk_shadow", data_root=leveraged_root,
            symbols=LEVERAGED_SHADOW_UNIVERSE, benchmark=benchmark,
            stop_loss_pct=0.07, trailing_stop_pct=0.10, max_holding_days=20,
            entry_fill_model="next-bar-limit", entry_limit_buffer_pct=0.03,
            events_csv=events_csv, assume_no_events=assume_no_events,
            weekend_exit_symbols=LEVERAGED_ETFS,
        )
        leveraged = run_variant(lev_cfg, simulate_fn=simulate_fn)
        if leveraged.error is not None:
            warnings.append(f"레버리지 ETF 셰도 skip: {leveraged.error}")

    return ExperimentReport(
        variants=(baseline_res, ext90, ext120), extension=extension,
        leveraged=leveraged, warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _variant_row(v: VariantResult) -> str:
    if v.error is not None:
        return f"  {v.name:<28}  [FAILED] {v.error}"
    return (
        f"  {v.name:<28}{_fmt(v.cumulative_return):>9}{_fmt(v.max_drawdown):>8}{_fmt(v.win_rate):>7}"
        f"{_fmt(v.total_pnl, '{:.2f}'):>11}{v.trades:>7}{_fmt(v.avg_holding_days, '{:.0f}'):>7}"
        f"{(v.longest_holding_days if v.longest_holding_days is not None else 0):>6}"
        f"{_fmt(v.return_mdd_ratio, '{:.2f}'):>8}{v.weekend_exit_count:>6}"
    )


def format_experiment(report: ExperimentReport) -> str:
    """실험 비교 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = ["=" * 100]
    lines.append("Trend Extension + Leveraged Weekend Risk (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 100)
    lines.append(
        f"  {'variant':<28}{'cum_ret':>9}{'MDD':>8}{'win':>7}{'total_PnL':>11}{'trades':>7}"
        f"{'avgHld':>7}{'maxHld':>6}{'ret/MDD':>8}{'wkEx':>6}"
    )
    for v in report.variants:
        lines.append(_variant_row(v))
    if report.leveraged is not None:
        lines.append("  -- leveraged shadow (separate universe) --")
        lines.append(_variant_row(report.leveraged))

    for v in (*report.variants, report.leveraged):
        if v is not None and v.error is None and v.exit_reason_dist:
            dist = ", ".join(f"{r}={n}" for r, n in v.exit_reason_dist)
            lines.append(f"  [{v.name}] exits: {dist}  top_symbol {v.top_symbol}({_fmt(v.top_symbol_pnl_share, '{:.0%}')})")

    ex = report.extension
    if ex is not None:
        lines.append("winner_extension (report-only):")
        lines.append(
            f"  profitable time_stop @60: {ex.profitable_time_stop_count} (pnl {ex.profitable_time_stop_pnl:.2f}), "
            f"losing time_stop: {ex.losing_time_stop_count}"
        )
        lines.append(
            f"  Δtotal_pnl 90d: {_fmt(ex.delta_total_pnl_90, '{:+.2f}')}  "
            f"120d: {_fmt(ex.delta_total_pnl_120, '{:+.2f}')}"
        )
        lines.append(f"  healthy conditions (report-only): {', '.join(ex.healthy_conditions)}")
        lines.append(f"  {ex.note}")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 100)
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="추세 연장 + 레버리지 주말리스크 실험(실주문 0)")
    p.add_argument("--universe-root", required=True, help="일반 유니버스 데이터 폴더")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--events-csv", default="data/events.csv")
    p.add_argument("--assume-no-events", action="store_true")
    p.add_argument("--leveraged-root", default=None, help="레버리지 ETF 데이터 폴더(있으면 셰도 실행)")
    p.add_argument("--output", default=None)
    return p


def run(args) -> int:
    report = run_trend_leverage_experiment(
        universe_root=args.universe_root, benchmark=args.benchmark,
        symbols=args.symbols,
        events_csv=(None if args.assume_no_events else args.events_csv),
        assume_no_events=args.assume_no_events, leveraged_root=args.leveraged_root,
    )
    text = format_experiment(report)
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
