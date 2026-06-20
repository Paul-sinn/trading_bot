"""baseline_comparison 테스트 (spec: specs/baseline_comparison.md).

전략 vs 단순 매수보유(SPY/QQQ/equal-weight/best-single hindsight) 비교(측정 전용). 결측 안전, 입력 불변.
real_orders=0. 네트워크 없음.
"""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agents.baseline_comparison import (
    BaselineComparison,
    BaselineResult,
    compute_baseline_comparison,
    format_baseline_comparison,
)

_DATES = pd.date_range("2025-01-01", periods=120, freq="B")


def _df(closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"close": closes}, index=_DATES[: len(closes)])


def _perf(cum, mdd, curve=None):
    return SimpleNamespace(
        cumulative_return=cum, max_drawdown=mdd,
        equity_curve=tuple(curve) if curve is not None else (1000.0, 1100.0, 1050.0, 1200.0),
    )


def _base(report, name):
    return next(b for b in report.baselines if b.name == name)


# --- 베이스라인 계산 ---


def test_spy_buy_hold_return_and_mdd():
    price = {"SPY": _df(np.linspace(100, 200, 120))}      # +100%, 단조상승 → MDD 0
    rep = compute_baseline_comparison(_perf(0.5, 0.10), price)
    assert isinstance(rep, BaselineComparison)
    spy = _base(rep, "SPY buy-hold")
    assert spy.cumulative_return == pytest.approx(1.0)
    assert spy.max_drawdown == pytest.approx(0.0, abs=1e-9)
    assert spy.return_diff_vs_strategy == pytest.approx(0.5 - 1.0)   # 전략 0.5 - SPY 1.0


def test_equal_weight_baseline():
    # A 1→2(+100%), B 1→1(0%) → 동일가중 곡선 1→1.5 (+50%).
    price = {
        "SPY": _df(np.linspace(100, 110, 120)),
        "A": _df(np.linspace(100, 200, 120)),
        "B": _df(np.full(120, 100.0)),
    }
    rep = compute_baseline_comparison(_perf(0.6, 0.05), price, universe=["A", "B"])
    eq = _base(rep, "equal-weight")
    assert eq.cumulative_return == pytest.approx(0.5)


def test_best_single_is_hindsight_labeled():
    price = {
        "SPY": _df(np.linspace(100, 110, 120)),
        "A": _df(np.linspace(100, 300, 120)),   # +200% (best)
        "B": _df(np.linspace(100, 120, 120)),   # +20%
    }
    rep = compute_baseline_comparison(_perf(0.5, 0.1), price, universe=["A", "B"])
    best = _base(rep, "best-single (hindsight)")
    assert best.hindsight is True
    assert best.symbol == "A"
    assert best.cumulative_return == pytest.approx(2.0)


def test_qqq_included_when_available():
    price = {"SPY": _df(np.linspace(100, 110, 120)), "QQQ": _df(np.linspace(100, 150, 120))}
    rep = compute_baseline_comparison(_perf(0.3, 0.1), price)
    qqq = _base(rep, "QQQ buy-hold")
    assert qqq.cumulative_return == pytest.approx(0.5)


def test_qqq_missing_handled_safely():
    price = {"SPY": _df(np.linspace(100, 110, 120))}      # QQQ 없음
    rep = compute_baseline_comparison(_perf(0.3, 0.1), price)
    qqq = _base(rep, "QQQ buy-hold")
    assert qqq.cumulative_return is None
    assert qqq.note is not None


def test_missing_spy_handled_safely():
    price = {"A": _df(np.linspace(100, 130, 120))}        # SPY 없음
    rep = compute_baseline_comparison(_perf(0.3, 0.1), price, universe=["A"])
    spy = _base(rep, "SPY buy-hold")
    assert spy.cumulative_return is None
    assert spy.note is not None
    assert rep.real_orders_placed == 0


# --- 경고 ---


def test_underperform_warning():
    price = {"SPY": _df(np.linspace(100, 200, 120))}      # SPY +100%
    rep = compute_baseline_comparison(_perf(0.10, 0.05), price)   # 전략 +10% < SPY
    assert any("SPY" in w and ("미달" in w or "underperform" in w.lower()) for w in rep.warnings)


def test_bull_market_warning():
    # passive equal-weight가 전략수익의 70%+ → 강세장 설명 경고.
    price = {
        "SPY": _df(np.linspace(100, 195, 120)),
        "A": _df(np.linspace(100, 195, 120)),
        "B": _df(np.linspace(100, 195, 120)),
    }
    rep = compute_baseline_comparison(_perf(1.0, 0.1), price, universe=["A", "B"])
    assert any("강세" in w or "시장" in w for w in rep.warnings)


def test_strategy_beats_baselines_no_warning():
    price = {"SPY": _df(np.linspace(100, 105, 120)), "A": _df(np.linspace(100, 102, 120))}
    rep = compute_baseline_comparison(_perf(0.50, 0.05), price, universe=["A"])
    assert not any("미달" in w for w in rep.warnings)


# --- fail-safe / 불변 ---


def test_no_universe_excludes_aux():
    price = {
        "SPY": _df(np.linspace(100, 110, 120)),
        "QQQ": _df(np.linspace(100, 120, 120)),
        "NVDA": _df(np.linspace(100, 200, 120)),
    }
    rep = compute_baseline_comparison(_perf(0.5, 0.1), price)   # universe 미지정
    best = _base(rep, "best-single (hindsight)")
    assert best.symbol == "NVDA"      # aux(SPY/QQQ) 제외하고 NVDA만 universe


def test_inputs_not_mutated():
    price = {"SPY": _df(np.linspace(100, 110, 120)), "A": _df(np.linspace(100, 150, 120))}
    spy_before = price["SPY"]["close"].copy()
    compute_baseline_comparison(_perf(0.5, 0.1), price, universe=["A"])
    pd.testing.assert_series_equal(price["SPY"]["close"], spy_before)


def test_real_orders_zero():
    price = {"SPY": _df(np.linspace(100, 110, 120))}
    rep = compute_baseline_comparison(_perf(0.5, 0.1), price)
    assert rep.real_orders_placed == 0


def test_format_contains_sections():
    price = {"SPY": _df(np.linspace(100, 200, 120)), "QQQ": _df(np.linspace(100, 150, 120))}
    rep = compute_baseline_comparison(_perf(0.3, 0.1), price)
    text = format_baseline_comparison(rep)
    assert "Baseline" in text
    assert "SPY buy-hold" in text
    assert "hindsight" in text.lower()
    assert "real_orders_placed : 0" in text
