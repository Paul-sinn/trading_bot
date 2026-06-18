"""알고리즘 — v1 일봉 백테스트 엔진 (순수·결정론, step0~4 오케스트레이션).

헌장 docs/STRATEGY.md §10: 헌장 전략을 과거 일봉에 재생해 §10 검증 사다리를 가능케 하고, 산출(승률·
손익비·표본수)을 Kelly 콜드스타트 입력으로 공급한다. v1 = 일봉 완전 전략(진입+청산+사이징+레짐+비용).

ADR-002: 부수효과 없는 순수 함수, 결정론적. I/O·네트워크·난수·전역상태 금지(입력 DataFrame만 소비).
미래참조 금지: 바 t 의사결정은 [:t+1]만, 체결은 다음날 시가(t+1). 비용·슬리피지 0 금지(부풀림 방지).
step0~4(signals/regime/entry/exits/sizing)를 재구현하지 않고 호출(단일 진실). LLM 실호출 금지.

spec: specs/backtest.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from algorithms.entry import breakout_entry, pullback_entry
from algorithms.exits import Bar, Position, evaluate_exit
from algorithms.filters import _atr
from algorithms.regime import classify_regime
from algorithms.sizing import position_size, regime_adjusted_fraction

_SURVIVORSHIP_WARNING = (
    "⚠️ v1은 현재 유니버스 + 무료 일봉데이터라 생존편향이 내장돼 결과는 '낙관적 상한'이다. "
    "엣지 유무를 거르는 fail-fast 용도지 라이브 greenlight가 아니다. 라이브 전 생존편향 없는 "
    "벤더(상폐종목+시점별 지수편입)로 point-in-time 재검증해야 한다(헌장 §3)."
)


# --- 설정 ---


@dataclass(frozen=True)
class CostModel:
    """보수적 거래비용. slippage_bps는 0 금지(일봉 체결 낙관 → 부풀림 방지)."""

    slippage_bps: float = 5.0
    commission: float = 0.0


@dataclass(frozen=True)
class BacktestParams:
    initial_capital: float = 100_000.0
    entry_mode: str = "pullback"  # "pullback" | "breakout"
    max_risk_pct: float = 0.01
    base_fraction: float = 1.0  # 콜드스타트 고정비율(켈리 미사용, 헌장 §7)
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    trail_atr_mult: float = 3.0
    warmup: int = 200
    periods_per_year: int = 252
    price_col: str = "close"
    fast: int = 50
    slow: int = 200
    rs_lookback: int = 63
    short_ma: int = 20
    pullback_window: int = 5
    breakout_lookback: int = 20


@dataclass(frozen=True)
class ExitLayers:
    """청산 레이어 토글 (헌장 §7-2 A/B 검증). 베이스라인 = ①스탑 + ④트레일."""

    use_breakeven: bool = True
    use_partial: bool = True
    use_trailing: bool = True
    use_regime_exit: bool = True
    use_time_stop: bool = True
    use_pre_earnings: bool = False  # 백테스트 기본 실적캘린더 없음


# --- 결과 ---


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float
    return_pct: float
    regime_at_entry: str
    reason: str


@dataclass(frozen=True)
class Benchmark:
    sharpe: float
    cagr: float
    max_drawdown: float


@dataclass(frozen=True)
class RegimePerformance:
    regime: str
    trades: int
    total_pnl: float


@dataclass(frozen=True)
class BacktestResult:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    win_loss_ratio: float
    sharpe: float
    sortino: float
    max_drawdown: float
    total_return: float
    cagr: float
    profit_factor: float
    expectancy: float
    benchmark: Benchmark
    trades: list[Trade]
    regime_breakdown: list[RegimePerformance]
    survivorship_warning: str = _SURVIVORSHIP_WARNING


# --- 내부 가변 포지션(루프 동안만; 결과는 frozen Trade) ---


@dataclass
class _Open:
    symbol: str
    entry_idx: int
    entry_price: float
    initial_stop: float
    current_stop: float
    qty: float
    highest: float
    partial_taken: bool
    cost_basis: float
    proceeds: float
    regime_at_entry: str
    last_reason: str = ""


# --- 지표 계산 (0분모 안전) ---


def _equity_metrics(equity: np.ndarray, periods_per_year: int) -> dict[str, float]:
    """equity 곡선 → sharpe/sortino/mdd/total_return/cagr (0분모·무변동 안전)."""
    out = {
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
        "total_return": 0.0,
        "cagr": 0.0,
    }
    if len(equity) < 2 or equity[0] <= 0:
        return out

    rets = np.diff(equity) / equity[:-1]
    if rets.size > 0 and rets.std(ddof=0) > 0:
        out["sharpe"] = float(
            rets.mean() / rets.std(ddof=0) * math.sqrt(periods_per_year)
        )
    downside = rets[rets < 0]
    if downside.size > 0 and downside.std(ddof=0) > 0:
        out["sortino"] = float(
            rets.mean() / downside.std(ddof=0) * math.sqrt(periods_per_year)
        )

    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    out["max_drawdown"] = float(-drawdown.min())  # 양수 분수

    out["total_return"] = float(equity[-1] / equity[0] - 1.0)
    years = len(equity) / periods_per_year
    if years > 0 and equity[-1] > 0:
        out["cagr"] = float((equity[-1] / equity[0]) ** (1.0 / years) - 1.0)
    return out


def _benchmark(spy_close: np.ndarray, periods_per_year: int) -> Benchmark:
    """SPY 매수후보유 벤치마크(동일 기간)."""
    m = _equity_metrics(spy_close, periods_per_year)
    return Benchmark(
        sharpe=m["sharpe"], cagr=m["cagr"], max_drawdown=m["max_drawdown"]
    )


def _trade_stats(trades: list[Trade]) -> dict[str, float]:
    """거래 리스트 → win_rate/win_loss_ratio/profit_factor/expectancy (0분모 안전)."""
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    n = len(trades)
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t.pnl for t in losses) / len(losses)) if losses else 0.0
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    return {
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n) if n else 0.0,
        "win_loss_ratio": (avg_win / avg_loss) if avg_loss > 0 else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else 0.0,
        "expectancy": (sum(t.pnl for t in trades) / n) if n else 0.0,
    }


def _regime_breakdown(trades: list[Trade]) -> list[RegimePerformance]:
    agg: dict[str, list[float]] = {}
    for t in trades:
        agg.setdefault(t.regime_at_entry, []).append(t.pnl)
    return [
        RegimePerformance(regime=r, trades=len(pnls), total_pnl=float(sum(pnls)))
        for r, pnls in sorted(agg.items())
    ]


# --- 엔진 ---


def run_backtest(
    price_data: dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    vix_series: pd.Series,
    *,
    params: BacktestParams = BacktestParams(),
    costs: CostModel = CostModel(),
    exit_layers: ExitLayers = ExitLayers(),
) -> BacktestResult:
    """v1 일봉 백테스트. 신호=종가 / 체결=다음날 시가 (미래참조 차단)."""
    symbols = sorted(price_data)
    pc = params.price_col
    n = len(spy_df)
    slip = costs.slippage_bps / 10_000.0

    spy_close = spy_df[pc].to_numpy(dtype=float)
    vix = pd.Series(vix_series, dtype="float64").to_numpy()

    # 심볼별 배열·ATR 사전계산(ewm은 causal → ATR[t]는 [:t]만 의존, 미래참조 없음).
    closes, opens, highs, lows, atrs = {}, {}, {}, {}, {}
    for s in symbols:
        df = price_data[s]
        closes[s] = df[pc].to_numpy(dtype=float)
        opens[s] = df["open"].to_numpy(dtype=float)
        highs[s] = df["high"].to_numpy(dtype=float)
        lows[s] = df["low"].to_numpy(dtype=float)
        atrs[s] = _atr(df, params.atr_period).to_numpy(dtype=float)

    cash = params.initial_capital
    positions: dict[str, _Open] = {}
    trades: list[Trade] = []
    equity_curve: list[float] = []

    for t in range(n):
        equity = cash + sum(p.qty * closes[s][t] for s, p in positions.items())
        equity_curve.append(equity)

        if t < params.warmup or t >= n - 1:
            continue

        regime = classify_regime(
            pd.Series(spy_close[: t + 1]),
            float(vix[t]),
            ma_period=params.slow,
        )

        # --- 청산 먼저 (다음날 시가 체결) ---
        for s in sorted(positions):
            p = positions[s]
            bar = Bar(high=highs[s][t], low=lows[s][t], close=closes[s][t])
            p.highest = max(p.highest, bar.high)
            pos_obj = Position(
                entry_price=p.entry_price,
                initial_stop=p.initial_stop,
                qty=p.qty,
                highest_since_entry=p.highest,
                current_stop=p.current_stop,
                partial_taken=p.partial_taken,
            )
            action = evaluate_exit(
                pos_obj,
                bar,
                regime=regime,
                days_held=t - p.entry_idx,
                atr=atrs[s][t],
                trail_atr_mult=params.trail_atr_mult,
                use_breakeven=exit_layers.use_breakeven,
                use_partial=exit_layers.use_partial,
                use_trailing=exit_layers.use_trailing,
                use_regime_exit=exit_layers.use_regime_exit,
                use_time_stop=exit_layers.use_time_stop,
                use_pre_earnings=exit_layers.use_pre_earnings,
            )
            if action.new_stop is not None:
                p.current_stop = action.new_stop
            if action.reason.startswith("③"):
                p.partial_taken = True

            if action.sell_fraction > 0:
                fill = opens[s][t + 1] * (1.0 - slip)  # 매도 = 불리한 가격
                sell_qty = p.qty * action.sell_fraction
                cash += sell_qty * fill - costs.commission
                p.proceeds += sell_qty * fill - costs.commission
                p.qty -= sell_qty
                p.last_reason = action.reason
                if action.sell_fraction >= 1.0 or p.qty <= 1e-9:
                    pnl = p.proceeds - p.cost_basis
                    trades.append(
                        Trade(
                            symbol=s,
                            entry_idx=p.entry_idx,
                            exit_idx=t + 1,
                            entry_price=p.entry_price,
                            exit_price=fill,
                            qty=p.cost_basis / p.entry_price if p.entry_price else 0.0,
                            pnl=pnl,
                            return_pct=(pnl / p.cost_basis) if p.cost_basis else 0.0,
                            regime_at_entry=p.regime_at_entry,
                            reason=action.reason,
                        )
                    )
                    del positions[s]

        # --- 진입 (다음날 시가 체결) ---
        for s in symbols:
            if s in positions:
                continue
            df_slice = price_data[s].iloc[: t + 1]
            spy_slice = spy_df.iloc[: t + 1]
            if params.entry_mode == "breakout":
                sig = breakout_entry(
                    df_slice, regime=regime, spy_df=spy_slice, price_col=pc,
                    fast=params.fast, slow=params.slow, rs_lookback=params.rs_lookback,
                    lookback=params.breakout_lookback,
                )
            else:
                sig = pullback_entry(
                    df_slice, regime=regime, spy_df=spy_slice, price_col=pc,
                    fast=params.fast, slow=params.slow, rs_lookback=params.rs_lookback,
                    short_ma=params.short_ma, window=params.pullback_window,
                )
            if not sig.enter:
                continue

            entry_fill = opens[s][t + 1] * (1.0 + slip)  # 매수 = 불리한 가격
            a = atrs[s][t]
            if not math.isfinite(a) or a <= 0:
                continue
            init_stop = entry_fill - a * params.atr_stop_mult
            kelly_f = regime_adjusted_fraction(params.base_fraction, regime)
            plan = position_size(
                equity, entry_fill, init_stop, params.max_risk_pct, kelly_f, 1.0
            )
            qty = float(plan.quantity)
            if qty <= 0:
                continue
            cost_basis = qty * entry_fill + costs.commission
            if cost_basis > cash:
                continue
            cash -= cost_basis
            positions[s] = _Open(
                symbol=s,
                entry_idx=t + 1,
                entry_price=entry_fill,
                initial_stop=init_stop,
                current_stop=init_stop,
                qty=qty,
                highest=entry_fill,
                partial_taken=False,
                cost_basis=cost_basis,
                proceeds=0.0,
                regime_at_entry=regime.value,
            )

    # --- 지표 ---
    eq = np.array(equity_curve[params.warmup :], dtype=float)
    if eq.size < 2:
        eq = np.array(equity_curve, dtype=float)
    m = _equity_metrics(eq, params.periods_per_year)
    stats = _trade_stats(trades)
    bench = _benchmark(spy_close[params.warmup :], params.periods_per_year)

    return BacktestResult(
        total_trades=len(trades),
        wins=int(stats["wins"]),
        losses=int(stats["losses"]),
        win_rate=stats["win_rate"],
        win_loss_ratio=stats["win_loss_ratio"],
        sharpe=m["sharpe"],
        sortino=m["sortino"],
        max_drawdown=m["max_drawdown"],
        total_return=m["total_return"],
        cagr=m["cagr"],
        profit_factor=stats["profit_factor"],
        expectancy=stats["expectancy"],
        benchmark=bench,
        trades=trades,
        regime_breakdown=_regime_breakdown(trades),
    )


def walk_forward(
    price_data: dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    vix_series: pd.Series,
    *,
    train_frac: float = 0.6,
    params: BacktestParams = BacktestParams(),
    costs: CostModel = CostModel(),
    exit_layers: ExitLayers = ExitLayers(),
) -> tuple[BacktestResult, BacktestResult]:
    """인·아웃샘플 분리 평가(헌장 §10②). 같은 params로 OOS 열화를 측정한다."""
    n = len(spy_df)
    split = int(n * train_frac)

    train = run_backtest(
        {s: df.iloc[:split] for s, df in price_data.items()},
        spy_df.iloc[:split],
        vix_series.iloc[:split],
        params=params,
        costs=costs,
        exit_layers=exit_layers,
    )
    # test: 전체 데이터 + warmup=split → split 이전을 워밍업으로 두고 [split:]만 거래.
    test_params = replace(params, warmup=max(params.warmup, split))
    test = run_backtest(
        price_data,
        spy_df,
        vix_series,
        params=test_params,
        costs=costs,
        exit_layers=exit_layers,
    )
    return train, test
