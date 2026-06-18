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


@dataclass(frozen=True)
class GateChecklist:
    sharpe_pass: bool
    beats_spy_sharpe: bool
    mdd_design_pass: bool
    mdd_hard_pass: bool
    overall_pass: bool
    sharpe: float
    benchmark_sharpe: float
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
    benchmark_sharpe: float,
    thresholds: GateThresholds = GateThresholds(),
) -> GateChecklist:
    """헌장 §9/§10 게이트 판정(판단 보조 — overall_pass여도 최종 판정은 사람)."""
    sharpe_pass = sharpe >= thresholds.sharpe_min
    beats_spy = sharpe > benchmark_sharpe
    mdd_design_pass = max_drawdown <= thresholds.mdd_design
    mdd_hard_pass = max_drawdown <= thresholds.mdd_hard
    return GateChecklist(
        sharpe_pass=sharpe_pass,
        beats_spy_sharpe=beats_spy,
        mdd_design_pass=mdd_design_pass,
        mdd_hard_pass=mdd_hard_pass,
        overall_pass=sharpe_pass and beats_spy and mdd_hard_pass,
        sharpe=sharpe,
        benchmark_sharpe=benchmark_sharpe,
        max_drawdown=max_drawdown,
        thresholds=thresholds,
    )


def calibrate_fraction(
    current_fraction: float, realized_mdd: float, mdd_target: float = 0.15
) -> FractionCalibration:
    """fraction을 MDD 설계목표(≤15%)로 역튜닝 제안(헌장 §6·§7). 적용은 사람 몫."""
    if realized_mdd > mdd_target:
        suggested = current_fraction * (mdd_target / realized_mdd)
        suggested = max(0.0, min(current_fraction, suggested))
        note = (
            f"실현 MDD {realized_mdd:.1%} > 목표 {mdd_target:.0%} → fraction을 "
            f"{current_fraction:.3f}에서 {suggested:.3f}로 축소 제안(적용은 사람)."
        )
    else:
        suggested = current_fraction
        note = (
            f"실현 MDD {realized_mdd:.1%} ≤ 목표 {mdd_target:.0%} → fraction 유지"
            f"(더 키우지 않음 — MDD governor)."
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
    params: BacktestParams = BacktestParams(),
    costs: CostModel = CostModel(),
    thresholds: GateThresholds = GateThresholds(),
) -> V1Report:
    """어댑터로 로드 → 엔진 실행 → 리포트 조립. go/no-go 판정은 사람 몫."""
    raw = {s: provider.get_ohlcv(s, start, end) for s in universe}
    spy = provider.get_ohlcv(spy_symbol, start, end)
    vix = provider.get_vix(start, end)
    price_data, spy_a, vix_a = _align(raw, spy, vix)

    strategy = run_backtest(
        price_data, spy_a, vix_a, params=params, costs=costs, exit_layers=ExitLayers()
    )
    ab = _exit_layer_ab(price_data, spy_a, vix_a, params, costs)
    gate = evaluate_gate(
        strategy.sharpe, strategy.max_drawdown, strategy.benchmark.sharpe, thresholds
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
    b = s.benchmark
    g = report.gate
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("⚠️ 생존편향 경고: " + report.survivorship_warning)
    lines.append("=" * 72)
    lines.append("")
    lines.append("[전략 vs SPY 벤치마크 — 위험조정 우위가 승리 (헌장 §9)]")
    lines.append(f"  {'지표':<14}{'전략':>14}{'SPY':>14}")
    lines.append(f"  {'Sharpe':<14}{s.sharpe:>14.3f}{b.sharpe:>14.3f}")
    lines.append(f"  {'Sortino':<14}{s.sortino:>14.3f}{'-':>14}")
    lines.append(f"  {'MaxDrawdown':<14}{s.max_drawdown:>14.2%}{b.max_drawdown:>14.2%}")
    lines.append(f"  {'CAGR':<14}{s.cagr:>14.2%}{b.cagr:>14.2%}")
    lines.append(f"  {'WinRate':<14}{s.win_rate:>14.2%}{'-':>14}")
    lines.append(f"  {'ProfitFactor':<14}{s.profit_factor:>14.3f}{'-':>14}")
    lines.append(f"  {'Expectancy':<14}{s.expectancy:>14.2f}{'-':>14}")
    lines.append(f"  {'Trades':<14}{s.total_trades:>14d}{'-':>14}")
    lines.append("")
    lines.append("[게이트 체크리스트 (헌장 §10) — 판단 보조, 최종 판정은 사람]")
    lines.append(f"  Sharpe ≥ {g.thresholds.sharpe_min}      : {'PASS' if g.sharpe_pass else 'FAIL'}")
    lines.append(f"  SPY 대비 Sharpe 우위   : {'PASS' if g.beats_spy_sharpe else 'FAIL'}")
    lines.append(f"  MDD ≤ {g.thresholds.mdd_design:.0%} (설계목표): {'PASS' if g.mdd_design_pass else 'FAIL'}")
    lines.append(f"  MDD ≤ {g.thresholds.mdd_hard:.0%} (하드차단): {'PASS' if g.mdd_hard_pass else 'FAIL'}")
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
