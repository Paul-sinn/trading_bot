"""생존편향 없는 약세장 OOS 재검증 러너 (agents 레이어, I/O).

헌장 docs/STRATEGY.md §10②(아웃샘플 — 커브피팅 킬러)·§3(생존편향 없는 벤더 재검증). point-in-time
유니버스(상폐종목 포함)로 약세장(2018/2022) 등 윈도우별 OOS 백테스트를 돌려, 편향 제거 後에도 엣지가
남는지 측정한다. working fraction은 보수적 max_risk_pct≈0.015. go/no-go 최종 판정은 사람.

ADR-001: I/O라 agents에 둔다. 실거래·자동 라이브 진입 코드 없음. 생존편향 제거·OOS 통과 전 greenlight 금지.

spec: specs/oos_validate.md
"""

from __future__ import annotations

from agents.data_adapter import PointInTimeProvider
from agents.v1_run import V1Report, run_v1
from algorithms.backtest import BacktestParams, CostModel


def run_oos_validation(
    provider: PointInTimeProvider,
    windows: dict[str, tuple[str, str]],
    *,
    max_risk_pct: float = 0.015,
    spy_symbol: str = "SPY",
    benchmark_symbols: tuple[str, ...] = ("QQQ", "SMH"),
    costs: CostModel = CostModel(),
) -> dict[str, V1Report]:
    """윈도우별 point-in-time OOS 백테스트를 실행해 {윈도우: V1Report}를 반환한다.

    각 윈도우의 start 시점 유니버스를 point-in-time(상폐종목 포함)로 잡아 run_v1을 돌린다.
    working fraction(max_risk_pct)은 보수적 기본 0.015 — 편향 든 v1 값을 그대로 쓰지 않는다(헌장 §6).
    """
    results: dict[str, V1Report] = {}
    for name, (start, end) in windows.items():
        universe = provider.get_constituents(start)  # point-in-time, 상폐종목 포함
        if not universe:
            continue
        results[name] = run_v1(
            provider,
            universe,
            start,
            end,
            spy_symbol=spy_symbol,
            benchmark_symbols=benchmark_symbols,
            params=BacktestParams(max_risk_pct=max_risk_pct),
            costs=costs,
        )
    return results


def format_oos_report(results: dict[str, V1Report]) -> str:
    """윈도우별 OOS 결과를 사람이 읽는 텍스트로. GO/NO-GO는 사람."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("생존편향 없는 point-in-time OOS 재검증 (상폐종목 포함·약세장, 헌장 §3·§10②)")
    lines.append("=" * 78)
    lines.append(
        f"  {'윈도우':<14}{'전략Sharpe':>11}{'전략CAGR':>10}{'MDD':>8}"
        f"{'QQQ_Shp':>9}{'QQQ_CAGR':>10}{'노출':>7}{'게이트':>8}"
    )
    for name, rep in results.items():
        s = rep.strategy
        qqq = s.benchmarks.get("QQQ")
        qqq_sharpe = f"{qqq.sharpe:.2f}" if qqq else "-"
        qqq_cagr = f"{qqq.cagr:.1%}" if qqq else "-"
        verdict = "PASS" if rep.gate.overall_pass else "FAIL"
        lines.append(
            f"  {name:<14}{s.sharpe:>11.2f}{s.cagr:>10.1%}{s.max_drawdown:>8.1%}"
            f"{qqq_sharpe:>9}{qqq_cagr:>10}{s.time_in_market_pct:>7.0%}{verdict:>8}"
        )
    lines.append("=" * 78)
    lines.append(
        "❗ GO/NO-GO는 사람: 약세장 포함·생존편향 제거 後에도 QQQ를 위험조정으로 이기는가? "
        "통과 → 페이퍼 → 소액 라이브. 자동 라이브 진입 없음."
    )
    lines.append("=" * 78)
    return "\n".join(lines)
