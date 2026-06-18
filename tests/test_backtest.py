"""Phase 5 step5 — v1 일봉 백테스트 엔진 테스트 (TDD Red→Green).

spec: specs/backtest.md  ·  상세: tasks/backtest-engine-prompt.md  ·  헌장: §9/§10
- 순수·결정론. 미래참조 차단(신호=종가, 체결=다음날 시가). 보수적 비용. SPY 벤치마크.
- step0~4 호출(재구현 없음). 청산 레이어 A/B. 생존편향 경고.
"""

import numpy as np
import pandas as pd

from algorithms.backtest import (
    BacktestResult,
    CostModel,
    ExitLayers,
    run_backtest,
    walk_forward,
)
from algorithms.sizing import effective_kelly_fraction


def _ohlcv(close: np.ndarray) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    # open = 직전 종가(갭 없음 가정), high/low = ±0.3%.
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.003,
            "low": np.minimum(open_, close) * 0.997,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )


def _uptrend_with_pullbacks(
    n: int = 340, up_len: int = 15, up: float = 1.0, dn_len: int = 6, dn: float = 1.5
) -> np.ndarray:
    # 강한 상승추세 + 깊은 주기적 눌림(20d선까지 닿아 눌림목 트리거 발생).
    # net drift = (15*1 - 6*1.5)/21 ≈ +0.29/bar → 80 → ~177.
    out = [80.0]
    mode_up = True
    run_len = 0
    for _ in range(1, n):
        if mode_up:
            out.append(out[-1] + up)
            run_len += 1
            if run_len >= up_len:
                mode_up = False
                run_len = 0
        else:
            out.append(out[-1] - dn)
            run_len += 1
            if run_len >= dn_len:
                mode_up = True
                run_len = 0
    return np.array(out)


def _choppy(n: int = 340) -> np.ndarray:
    x = np.linspace(0, 30 * np.pi, n)
    return 120 + 8 * np.sin(x)


def _weak_uptrend(n: int = 340) -> np.ndarray:
    return np.linspace(100, 120, n)


def _make_inputs(symbol_close: np.ndarray, vix_level: float = 15.0):
    n = len(symbol_close)
    price_data = {"AAA": _ohlcv(symbol_close)}
    spy_df = _ohlcv(_weak_uptrend(n))
    vix = pd.Series(np.full(n, vix_level))
    return price_data, spy_df, vix


# --- 기본 산출 ---


def test_run_backtest_returns_result():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    res = run_backtest(pd_, spy, vix)
    assert isinstance(res, BacktestResult)
    assert res.total_trades >= 0
    assert 0.0 <= res.win_rate <= 1.0


def test_survivorship_warning_present():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    res = run_backtest(pd_, spy, vix)
    assert isinstance(res.survivorship_warning, str)
    assert res.survivorship_warning  # 비어있지 않음


def test_benchmark_present():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    res = run_backtest(pd_, spy, vix)
    assert hasattr(res.benchmark, "sharpe")
    assert hasattr(res.benchmark, "max_drawdown")


# --- 결정론 ---


def test_deterministic_two_runs_identical():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    r1 = run_backtest(pd_, spy, vix)
    r2 = run_backtest(pd_, spy, vix)
    assert r1.total_trades == r2.total_trades
    assert r1.total_return == r2.total_return
    assert r1.sharpe == r2.sharpe


# --- 미래참조 차단 ---


def test_no_lookahead_future_bars_dont_change_past():
    base = _uptrend_with_pullbacks(n=320)
    pd_a, spy_a, vix_a = _make_inputs(base)
    res_a = run_backtest(pd_a, spy_a, vix_a)

    # 미래 바 40개 추가(과거와 무관해야 함).
    extra = base[-1] + np.cumsum(np.full(40, -3.0))  # 급락하는 미래
    base_ext = np.concatenate([base, extra])
    pd_b, spy_b, vix_b = _make_inputs(base_ext)
    res_b = run_backtest(pd_b, spy_b, vix_b)

    # 원래 기간(인덱스 < 320)에 진입한 거래는 동일해야 한다.
    past_a = [(t.symbol, t.entry_idx, t.exit_idx, round(t.pnl, 6)) for t in res_a.trades if t.exit_idx < 319]
    past_b = [(t.symbol, t.entry_idx, t.exit_idx, round(t.pnl, 6)) for t in res_b.trades if t.exit_idx < 319]
    assert past_a == past_b


# --- 비용 단조성 ---


def test_higher_costs_monotonically_worse():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    low = run_backtest(pd_, spy, vix, costs=CostModel(slippage_bps=5.0))
    high = run_backtest(pd_, spy, vix, costs=CostModel(slippage_bps=50.0))
    assert high.total_return <= low.total_return + 1e-9


# --- 엣지/추세 ---


def test_uptrend_positive_total_return():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    res = run_backtest(pd_, spy, vix)
    assert res.total_trades > 0
    assert res.total_return > 0.0


def test_choppy_underperforms_uptrend():
    up_pd, up_spy, up_vix = _make_inputs(_uptrend_with_pullbacks())
    ch_pd, ch_spy, ch_vix = _make_inputs(_choppy())
    up = run_backtest(up_pd, up_spy, up_vix)
    ch = run_backtest(ch_pd, ch_spy, ch_vix)
    assert ch.total_return <= up.total_return


def test_panic_regime_blocks_all_entries():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks(), vix_level=35.0)  # VIX>30 → D
    res = run_backtest(pd_, spy, vix)
    assert res.total_trades == 0


# --- 0거래 안전 ---


def test_flat_data_zero_trades_safe():
    flat = np.full(300, 100.0)
    pd_, spy, vix = _make_inputs(flat)
    res = run_backtest(pd_, spy, vix)
    assert res.total_trades == 0
    assert res.win_rate == 0.0
    assert res.sharpe == 0.0  # 0분모 폭발 없이 0.


# --- 켈리 연결 ---


def test_result_feeds_effective_kelly_within_cap():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    res = run_backtest(pd_, spy, vix)
    f = effective_kelly_fraction(res.win_rate, res.win_loss_ratio, res.total_trades)
    assert 0.0 <= f <= 0.25


# --- 청산 레이어 A/B ---


def test_exit_layers_ab_changes_result():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks())
    baseline = run_backtest(
        pd_, spy, vix,
        exit_layers=ExitLayers(
            use_breakeven=False, use_partial=False, use_regime_exit=False,
            use_time_stop=False, use_trailing=True,
        ),
    )
    full = run_backtest(pd_, spy, vix, exit_layers=ExitLayers())
    # 부분익절·본전 등이 켜지면 결과(수익률 또는 거래수)가 달라진다.
    assert (baseline.total_return != full.total_return) or (
        baseline.total_trades != full.total_trades
    )


# --- 워크포워드 ---


def test_walk_forward_returns_train_test():
    pd_, spy, vix = _make_inputs(_uptrend_with_pullbacks(n=400))
    train, test = walk_forward(pd_, spy, vix, train_frac=0.6)
    assert isinstance(train, BacktestResult)
    assert isinstance(test, BacktestResult)
