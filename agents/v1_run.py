"""v1 백테스트 실행 + 리포트 + go/no-go 판단 근거 (agents 레이어).

헌장 docs/STRATEGY.md §10: step6 어댑터로 무료 일봉을 받아 step5 엔진으로 v1 백테스트를 실행하고
매매일지·성과 리포트·게이트 체크리스트를 산출한다. ⚠️ go/no-go 최종 판정은 사람이 한다 — 이 모듈은
판단 근거(숫자)만 만든다. 실거래·실주문·자동 라이브 진입 코드는 여기 없다(연구·측정 단계).

I/O(어댑터 호출)라 agents에 둔다(ADR-001). 게이트·캘리브레이션 집계는 순수 함수로 분리(테스트).
provider 주입형 — 테스트는 MockDailyProvider로 네트워크 없이 검증.

spec: specs/v1_run.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agents.data_adapter import SURVIVORSHIP_WARNING, DailyDataProvider
from algorithms.backtest import (
    BacktestParams,
    BacktestResult,
    Benchmark,
    CostModel,
    ExitLayers,
    run_backtest,
)


@dataclass(frozen=True)
class GateThresholds:
    """헌장 §10/§6 검증 게이트 임계값(시작값 — §12 최종화 OPEN)."""

    sharpe_min: float = 1.0
    mdd_design: float = 0.15
    mdd_hard: float = 0.20
    abs_return_floor: float = 0.5  # 전략 CAGR ≥ 최고 벤치마크 CAGR × 이 비율(절대수익 점검, 헌장 §9)


@dataclass(frozen=True)
class GateChecklist:
    sharpe_pass: bool
    beats_benchmarks: bool  # QQQ·SMH 중 강한 쪽 Sharpe 대비 우위 (헌장 §9)
    abs_return_ok: bool  # 절대수익이 인덱스에 크게 안 뒤짐
    mdd_design_pass: bool
    mdd_hard_pass: bool
    overall_pass: bool
    sharpe: float
    cagr: float
    toughest_benchmark_sharpe: float
    best_benchmark_cagr: float
    max_drawdown: float
    thresholds: GateThresholds


@dataclass(frozen=True)
class ExitLayerAB:
    name: str
    total_return: float
    sharpe: float
    max_drawdown: float
    total_trades: int


@dataclass(frozen=True)
class FractionCalibration:
    current_fraction: float
    realized_mdd: float
    mdd_target: float
    suggested_fraction: float
    note: str


@dataclass(frozen=True)
class V1Report:
    survivorship_warning: str
    strategy: BacktestResult
    gate: GateChecklist
    exit_layer_ab: list[ExitLayerAB]
    fraction_calibration: FractionCalibration


# --- 순수 집계 로직 ---


def evaluate_gate(
    sharpe: float,
    max_drawdown: float,
    cagr: float,
    benchmarks: dict[str, "Benchmark"],
    thresholds: GateThresholds = GateThresholds(),
) -> GateChecklist:
    """헌장 §9/§10 게이트 판정 — QQQ/SMH 기준(SPY 단독 아님). 최종 판정은 사람."""
    # 가장 빡센 경쟁자: QQQ·SMH(있으면) 중 강한 쪽, 없으면 전체 벤치마크 중 max.
    competitors = [
        b for name, b in benchmarks.items() if name in ("QQQ", "SMH")
    ] or list(benchmarks.values())
    toughest_sharpe = max((b.sharpe for b in competitors), default=0.0)
    best_cagr = max((b.cagr for b in benchmarks.values()), default=0.0)

    sharpe_pass = sharpe >= thresholds.sharpe_min
    beats_benchmarks = sharpe > toughest_sharpe
    abs_return_ok = cagr >= best_cagr * thresholds.abs_return_floor
    mdd_design_pass = max_drawdown <= thresholds.mdd_design
    mdd_hard_pass = max_drawdown <= thresholds.mdd_hard
    return GateChecklist(
        sharpe_pass=sharpe_pass,
        beats_benchmarks=beats_benchmarks,
        abs_return_ok=abs_return_ok,
        mdd_design_pass=mdd_design_pass,
        mdd_hard_pass=mdd_hard_pass,
        overall_pass=(
            sharpe_pass and beats_benchmarks and abs_return_ok and mdd_hard_pass
        ),
        sharpe=sharpe,
        cagr=cagr,
        toughest_benchmark_sharpe=toughest_sharpe,
        best_benchmark_cagr=best_cagr,
        max_drawdown=max_drawdown,
        thresholds=thresholds,
    )


def calibrate_fraction(
    current_fraction: float,
    realized_mdd: float,
    mdd_target: float = 0.15,
    *,
    mdd_floor: float = 0.12,
) -> FractionCalibration:
    """공격성(=max_risk_pct/fraction)을 MDD 예산 밴드[mdd_floor, mdd_target]로 양방향 제안(헌장 §6).

    MDD > target → 축소 / MDD < floor → 상향(예산 미사용) / band 내 → 유지. realized_mdd≤0 → 유지. 적용은 사람.
    """
    if realized_mdd <= 0:
        suggested = current_fraction
        note = f"실현 MDD {realized_mdd:.1%} → 측정 불가, fraction 유지."
    elif realized_mdd > mdd_target:
        suggested = max(0.0, current_fraction * (mdd_target / realized_mdd))
        note = (
            f"실현 MDD {realized_mdd:.1%} > 목표 {mdd_target:.0%} → fraction "
            f"{current_fraction:.3f}→{suggested:.3f} 축소 제안(적용은 사람)."
        )
    elif realized_mdd < mdd_floor:
        suggested = current_fraction * (mdd_floor / realized_mdd)
        note = (
            f"실현 MDD {realized_mdd:.1%} < 하한 {mdd_floor:.0%} → 예산 미사용 → fraction "
            f"{current_fraction:.3f}→{suggested:.3f} 상향 제안(편향없는 데이터서 확정)."
        )
    else:
        suggested = current_fraction
        note = (
            f"실현 MDD {realized_mdd:.1%} ∈ 예산밴드[{mdd_floor:.0%},{mdd_target:.0%}] → fraction 유지."
        )
    return FractionCalibration(
        current_fraction=current_fraction,
        realized_mdd=realized_mdd,
        mdd_target=mdd_target,
        suggested_fraction=suggested,
        note=note,
    )


def _align(
    raw: dict[str, pd.DataFrame], spy: pd.DataFrame, vix: pd.Series
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.Series]:
    """universe + SPY + VIX를 공통 날짜 인덱스(교집합)로 정렬(엔진 정렬 가정 충족)."""
    common = spy.index
    for df in raw.values():
        common = common.intersection(df.index)
    common = common.intersection(vix.index).sort_values()
    price_data = {s: df.reindex(common) for s, df in raw.items()}
    return price_data, spy.reindex(common), vix.reindex(common)


_EXIT_LAYER_CONFIGS: list[tuple[str, ExitLayers]] = [
    (
        "baseline (①스탑+④트레일)",
        ExitLayers(
            use_breakeven=False, use_partial=False, use_regime_exit=False,
            use_time_stop=False, use_pre_earnings=False, use_trailing=True,
        ),
    ),
    (
        "+② 본전 스탑",
        ExitLayers(
            use_breakeven=True, use_partial=False, use_regime_exit=False,
            use_time_stop=False, use_pre_earnings=False, use_trailing=True,
        ),
    ),
    (
        "+③ 부분 익절",
        ExitLayers(
            use_breakeven=True, use_partial=True, use_regime_exit=False,
            use_time_stop=False, use_pre_earnings=False, use_trailing=True,
        ),
    ),
    (
        "+⑤⑦ 레짐/타임 (full)",
        ExitLayers(
            use_breakeven=True, use_partial=True, use_regime_exit=True,
            use_time_stop=True, use_pre_earnings=False, use_trailing=True,
        ),
    ),
]


def _exit_layer_ab(
    price_data: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    vix: pd.Series,
    params: BacktestParams,
    costs: CostModel,
) -> list[ExitLayerAB]:
    """청산 레이어 A/B(헌장 §7-2 경고3): 베이스라인 → 레이어별 누적 추가."""
    results = []
    for name, layers in _EXIT_LAYER_CONFIGS:
        r = run_backtest(price_data, spy, vix, params=params, costs=costs, exit_layers=layers)
        results.append(
            ExitLayerAB(
                name=name,
                total_return=r.total_return,
                sharpe=r.sharpe,
                max_drawdown=r.max_drawdown,
                total_trades=r.total_trades,
            )
        )
    return results


def run_v1(
    provider: DailyDataProvider,
    universe: list[str],
    start: str | None = None,
    end: str | None = None,
    *,
    spy_symbol: str = "SPY",
    benchmark_symbols: tuple[str, ...] = ("QQQ", "SMH"),
    params: BacktestParams = BacktestParams(),
    costs: CostModel = CostModel(),
    thresholds: GateThresholds = GateThresholds(),
) -> V1Report:
    """어댑터로 로드 → 엔진 실행 → 리포트 조립. go/no-go 판정은 사람 몫."""
    raw = {s: provider.get_ohlcv(s, start, end) for s in universe}
    spy = provider.get_ohlcv(spy_symbol, start, end)
    vix = provider.get_vix(start, end)
    price_data, spy_a, vix_a = _align(raw, spy, vix)

    # 다중 벤치마크(QQQ·SMH 등) 로드 후 공통 인덱스로 정렬(헌장 §9). 어댑터에 없으면 생략.
    benchmark_data: dict = {}
    for sym in benchmark_symbols:
        try:
            bdf = provider.get_ohlcv(sym, start, end)
        except KeyError:
            continue
        benchmark_data[sym] = bdf.reindex(spy_a.index)

    strategy = run_backtest(
        price_data, spy_a, vix_a, params=params, costs=costs,
        exit_layers=ExitLayers(), benchmark_data=benchmark_data,
    )
    ab = _exit_layer_ab(price_data, spy_a, vix_a, params, costs)
    gate = evaluate_gate(
        strategy.sharpe,
        strategy.max_drawdown,
        strategy.cagr,
        strategy.benchmarks,
        thresholds,
    )
    calib = calibrate_fraction(
        params.base_fraction, strategy.max_drawdown, thresholds.mdd_design
    )
    return V1Report(
        survivorship_warning=SURVIVORSHIP_WARNING,
        strategy=strategy,
        gate=gate,
        exit_layer_ab=ab,
        fraction_calibration=calib,
    )


def format_report(report: V1Report) -> str:
    """사람이 읽는 리포트 텍스트. 최종 GO/NO-GO는 사람이 판정."""
    s = report.strategy
    g = report.gate
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("⚠️ 생존편향 경고: " + report.survivorship_warning)
    lines.append("=" * 72)
    lines.append("")
    # 전략 vs 다중 벤치마크(SPY/QQQ/SMH 등) 나란히 — 위험조정 우위가 승리 (헌장 §9).
    bench_names = list(s.benchmarks)
    lines.append("[전략 vs 벤치마크 — 위험조정 우위가 승리 (헌장 §9: SPY+QQQ+SMH)]")
    header = f"  {'지표':<14}{'전략':>12}" + "".join(f"{name:>12}" for name in bench_names)
    lines.append(header)
    lines.append(
        f"  {'Sharpe':<14}{s.sharpe:>12.3f}"
        + "".join(f"{s.benchmarks[n].sharpe:>12.3f}" for n in bench_names)
    )
    lines.append(
        f"  {'CAGR':<14}{s.cagr:>12.2%}"
        + "".join(f"{s.benchmarks[n].cagr:>12.2%}" for n in bench_names)
    )
    lines.append(
        f"  {'MaxDrawdown':<14}{s.max_drawdown:>12.2%}"
        + "".join(f"{s.benchmarks[n].max_drawdown:>12.2%}" for n in bench_names)
    )
    lines.append(f"  {'TotalReturn':<14}{s.total_return:>12.2%}")
    lines.append(f"  {'Sortino':<14}{s.sortino:>12.3f}")
    lines.append(f"  {'WinRate':<14}{s.win_rate:>12.2%}")
    lines.append(f"  {'ProfitFactor':<14}{s.profit_factor:>12.3f}")
    lines.append(f"  {'Expectancy':<14}{s.expectancy:>12.2f}")
    lines.append(f"  {'Trades':<14}{s.total_trades:>12d}")
    lines.append("")
    # 노출도(time-in-market) — 현금에 앉아 Sharpe만 샀는지 점검 (헌장 §9).
    lines.append("[노출도 (time-in-market) — 절대수익/현금비중 점검 (헌장 §9)]")
    lines.append(
        f"  time_in_market={s.time_in_market_pct:.1%}  "
        f"avg_concurrent_positions={s.avg_concurrent_positions:.2f}"
    )
    lines.append("")
    lines.append("[게이트 체크리스트 (헌장 §9/§10) — QQQ/SMH 기준, 판단 보조, 최종 판정은 사람]")
    lines.append(f"  Sharpe ≥ {g.thresholds.sharpe_min}              : {'PASS' if g.sharpe_pass else 'FAIL'}")
    lines.append(
        f"  QQQ/SMH 대비 Sharpe 우위   : {'PASS' if g.beats_benchmarks else 'FAIL'}"
        f"  (전략 {g.sharpe:.2f} vs 최강 {g.toughest_benchmark_sharpe:.2f})"
    )
    lines.append(
        f"  절대수익 인덱스 대비 양호  : {'PASS' if g.abs_return_ok else 'FAIL'}"
        f"  (전략 CAGR {g.cagr:.1%} vs 최고 {g.best_benchmark_cagr:.1%} × {g.thresholds.abs_return_floor:.0%})"
    )
    lines.append(f"  MDD ≤ {g.thresholds.mdd_design:.0%} (설계목표)    : {'PASS' if g.mdd_design_pass else 'FAIL'}")
    lines.append(f"  MDD ≤ {g.thresholds.mdd_hard:.0%} (하드차단)    : {'PASS' if g.mdd_hard_pass else 'FAIL'}")
    lines.append("")
    lines.append("[청산 레이어 A/B (헌장 §7-2 — 각 레이어가 Sharpe 개선하며 총수익 안 죽일 때만 채택)]")
    lines.append(f"  {'레이어':<24}{'TotalRet':>12}{'Sharpe':>10}{'MDD':>10}{'Trades':>8}")
    for ab in report.exit_layer_ab:
        lines.append(
            f"  {ab.name:<24}{ab.total_return:>12.2%}{ab.sharpe:>10.3f}"
            f"{ab.max_drawdown:>10.2%}{ab.total_trades:>8d}"
        )
    lines.append("")
    lines.append("[fraction 캘리브레이션 (MDD governor, 헌장 §6·§7)]")
    lines.append("  " + report.fraction_calibration.note)
    lines.append("")
    lines.append("[매매일지 요약]")
    lines.append(f"  총 {s.total_trades}건 / 승 {s.wins} / 패 {s.losses} / 승률 {s.win_rate:.1%}")
    for r in s.regime_breakdown:
        lines.append(f"   · 레짐 {r.regime}: {r.trades}건, 누적손익 {r.total_pnl:.2f}")
    lines.append("")
    lines.append("=" * 72)
    verdict = "조건 충족(검토 가능)" if g.overall_pass else "조건 미충족"
    lines.append(f"게이트 자동판정(보조): {verdict}.")
    lines.append(
        "❗ GO/NO-GO 최종 결정은 사람이 한다. v1 통과는 라이브 greenlight가 아니다 — "
        "다음은 생존편향 없는 벤더 재검증 → 페이퍼 → 소액 라이브(헌장 §3·§10). 자동 라이브 진입 없음."
    )
    lines.append("=" * 72)
    return "\n".join(lines)
