"""벤치마크/베이스라인 비교 — 전략이 단순 매수보유를 넘어 가치를 더하는지 본다(순수 측정).

전략 성과(performance) + price_data만 읽어 SPY/QQQ/equal-weight/best-single(hindsight) 매수보유와
비교한다. 상태/매매/veto를 바꾸지 않는다 — 베이스라인은 실 매매에 쓰지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/baseline_comparison.md
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd

_AUX = ("VIX",)                 # universe 자동 추론 시 제외(벤치마크는 인자로 따로 제외).
_TRADING_DAYS = 252
_BULL_FRACTION = 0.7            # passive 베이스라인이 전략수익의 70%+면 강세장 설명 경고.


@dataclass(frozen=True)
class BaselineResult:
    """한 베이스라인의 매수보유 성과."""

    name: str
    symbol: str | None
    cumulative_return: float | None
    max_drawdown: float | None
    volatility: float | None
    return_diff_vs_strategy: float | None
    mdd_diff_vs_strategy: float | None
    hindsight: bool = False
    note: str | None = None


@dataclass(frozen=True)
class BaselineComparison:
    """전략 vs 베이스라인 비교 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    strategy_return: float
    strategy_max_drawdown: float
    strategy_volatility: float | None
    baselines: tuple[BaselineResult, ...]
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _window_close(df, start, end) -> pd.Series | None:
    """df['close']를 [start,end]로 슬라이스해 NaN 제거(점 2개 미만이면 None)."""
    if df is None or "close" not in getattr(df, "columns", []):
        return None
    s = df["close"]
    if start is not None or end is not None:
        s = s.loc[start:end]
    s = s.dropna()
    return s if len(s) >= 2 else None


def _bh_return(s: pd.Series) -> float:
    return float(s.iloc[-1] / s.iloc[0] - 1.0)


def _mdd(s: pd.Series) -> float:
    """종가 곡선의 최대 낙폭(비율, 양수)."""
    peak = float("-inf")
    mdd = 0.0
    for v in s:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def _vol(s: pd.Series) -> float | None:
    """일수익 표준편차 × √252(연율화). 점 부족하면 None."""
    rets = s.pct_change().dropna()
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=0) * math.sqrt(_TRADING_DAYS))


def _curve_vol(curve) -> float | None:
    """전략 equity_curve(float 시퀀스)의 연율화 변동성."""
    if not curve or len(curve) < 2:
        return None
    rets = [b / a - 1.0 for a, b in zip(curve, curve[1:]) if a > 0]
    if len(rets) < 2:
        return None
    return float(statistics.pstdev(rets) * math.sqrt(_TRADING_DAYS))


def _result(name, s, *, symbol, strat_ret, strat_mdd, hindsight=False, missing_note=None):
    """종가 시리즈 s로 BaselineResult를 만든다. s=None이면 결측 처리(note)."""
    if s is None:
        return BaselineResult(
            name=name, symbol=symbol, cumulative_return=None, max_drawdown=None,
            volatility=None, return_diff_vs_strategy=None, mdd_diff_vs_strategy=None,
            hindsight=hindsight, note=missing_note or "데이터 없음",
        )
    ret = _bh_return(s)
    mdd = _mdd(s)
    return BaselineResult(
        name=name, symbol=symbol, cumulative_return=ret, max_drawdown=mdd,
        volatility=_vol(s), return_diff_vs_strategy=strat_ret - ret,
        mdd_diff_vs_strategy=strat_mdd - mdd, hindsight=hindsight, note=None,
    )


def _equal_weight_curve(price_data, universe, start, end) -> pd.Series | None:
    """universe 동일가중 매수보유 곡선(정규화 종가 평균, 교집합 날짜)."""
    norm = []
    for sym in universe:
        s = _window_close(price_data.get(sym), start, end)
        if s is not None and s.iloc[0] > 0:
            norm.append(s / s.iloc[0])
    if not norm:
        return None
    aligned = pd.concat(norm, axis=1).dropna()
    if len(aligned) < 2:
        return None
    return aligned.mean(axis=1)


def compute_baseline_comparison(
    performance,
    price_data,
    *,
    universe=None,
    start=None,
    end=None,
    benchmark_symbol: str = "SPY",
    qqq_symbol: str = "QQQ",
) -> BaselineComparison:
    """전략 성과를 단순 매수보유 베이스라인과 비교한다(읽기 전용 — 입력 불변)."""
    strat_ret = float(performance.cumulative_return)
    strat_mdd = float(performance.max_drawdown)
    strat_vol = _curve_vol(getattr(performance, "equity_curve", None))

    price_data = price_data or {}
    if universe is None:
        excluded = {benchmark_symbol, qqq_symbol, *_AUX}
        universe = [s for s in price_data if s not in excluded]

    baselines: list[BaselineResult] = []

    # SPY / QQQ 매수보유.
    baselines.append(_result(
        "SPY buy-hold", _window_close(price_data.get(benchmark_symbol), start, end),
        symbol=benchmark_symbol, strat_ret=strat_ret, strat_mdd=strat_mdd,
        missing_note=f"{benchmark_symbol} 데이터 없음",
    ))
    baselines.append(_result(
        "QQQ buy-hold", _window_close(price_data.get(qqq_symbol), start, end),
        symbol=qqq_symbol, strat_ret=strat_ret, strat_mdd=strat_mdd,
        missing_note=f"{qqq_symbol} 데이터 없음(선택 베이스라인)",
    ))

    # equal-weight.
    eq_curve = _equal_weight_curve(price_data, universe, start, end)
    baselines.append(_result(
        "equal-weight", eq_curve, symbol=None, strat_ret=strat_ret, strat_mdd=strat_mdd,
        missing_note="universe 데이터 없음",
    ))

    # best-single (hindsight).
    best_sym = None
    best_ret = None
    best_series = None
    for sym in universe:
        s = _window_close(price_data.get(sym), start, end)
        if s is None:
            continue
        r = _bh_return(s)
        if best_ret is None or r > best_ret:
            best_ret, best_sym, best_series = r, sym, s
    baselines.append(_result(
        "best-single (hindsight)", best_series, symbol=best_sym,
        strat_ret=strat_ret, strat_mdd=strat_mdd, hindsight=True,
        missing_note="universe 데이터 없음",
    ))

    # 경고: 단순 매수보유 미달(hindsight 제외) + 강세장 설명.
    simple = [b for b in baselines if not b.hindsight and b.cumulative_return is not None]
    warnings: list[str] = []
    for b in simple:
        if strat_ret < b.cumulative_return:
            warnings.append(
                f"전략({strat_ret:.2%})이 {b.name}({b.cumulative_return:.2%}) 매수보유에 미달"
            )
    passive_returns = [b.cumulative_return for b in simple]
    if passive_returns and strat_ret > 0:
        best_passive = max(passive_returns)
        if best_passive >= _BULL_FRACTION * strat_ret:
            warnings.append(
                f"성과 상당부분이 시장/섹터 강세로 설명될 수 있음 "
                f"(passive 최고 {best_passive:.2%} vs 전략 {strat_ret:.2%})"
            )

    return BaselineComparison(
        strategy_return=strat_ret,
        strategy_max_drawdown=strat_mdd,
        strategy_volatility=strat_vol,
        baselines=tuple(baselines),
        warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_baseline_comparison(report: BaselineComparison) -> str:
    """사람이 읽는 베이스라인 비교 텍스트(측정 보조 — 판단 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("Baseline Comparison (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 78)
    lines.append(
        f"strategy: return {_fmt(report.strategy_return)}  MDD {_fmt(report.strategy_max_drawdown)}  "
        f"vol {_fmt(report.strategy_volatility)}"
    )
    lines.append(
        f"  {'baseline':<26}{'return':>9}{'MDD':>8}{'vol':>8}{'Δret(strat-base)':>18}{'ΔMDD':>9}"
    )
    for b in report.baselines:
        label = b.name + (f" [{b.symbol}]" if b.symbol else "")
        if b.cumulative_return is None:
            lines.append(f"  {label:<26}  ({b.note})")
            continue
        lines.append(
            f"  {label:<26}{_fmt(b.cumulative_return):>9}{_fmt(b.max_drawdown):>8}"
            f"{_fmt(b.volatility):>8}{_fmt(b.return_diff_vs_strategy, '{:+.2%}'):>18}"
            f"{_fmt(b.mdd_diff_vs_strategy, '{:+.2%}'):>9}"
        )
    lines.append("  (best-single = 사후 최고 단일종목 — hindsight only, 달성 불가 기준선)")

    if report.warnings:
        lines.append("warnings:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 78)
    return "\n".join(lines)
