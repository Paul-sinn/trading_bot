"""Phase 5 step7 — v1 실행·리포트·게이트 테스트 (TDD Red→Green).

spec: specs/v1_run.md  ·  헌장: §6/§9/§10
- 네트워크 금지: MockDailyProvider 주입.
- SPY 벤치마크·청산 레이어 A/B·게이트 체크리스트·생존편향 경고가 리포트에 존재.
- go/no-go는 사람 몫 — 자동 라이브 진입 없음(이 모듈엔 주문 코드 없음).
"""

import numpy as np
import pandas as pd

from agents.data_adapter import MockDailyProvider
from agents.v1_run import (
    V1Report,
    calibrate_fraction,
    evaluate_gate,
    format_report,
    run_v1,
)
from algorithms.backtest import Benchmark


def _ohlcv(close: np.ndarray) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "open": open_, "high": np.maximum(open_, close) * 1.003,
            "low": np.minimum(open_, close) * 0.997, "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _uptrend_with_pullbacks(n: int = 340) -> np.ndarray:
    out = [80.0]
    mode_up, run_len = True, 0
    for _ in range(1, n):
        if mode_up:
            out.append(out[-1] + 1.0); run_len += 1
            if run_len >= 15: mode_up, run_len = False, 0
        else:
            out.append(out[-1] - 1.5); run_len += 1
            if run_len >= 6: mode_up, run_len = True, 0
    return np.array(out)


def _provider(n: int = 340) -> MockDailyProvider:
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    frames = {
        "AAA": _ohlcv(_uptrend_with_pullbacks(n)),
        "SPY": _ohlcv(np.linspace(100, 120, n)),
        "QQQ": _ohlcv(np.linspace(100, 140, n)),
        "SMH": _ohlcv(np.linspace(100, 180, n)),
    }
    vix = pd.Series(np.full(n, 15.0), index=idx)
    return MockDailyProvider(frames, vix=vix)


# --- evaluate_gate (순수 로직, step10 — QQQ/SMH 기준) ---


def _benchmarks(spy=0.7, qqq=0.8, smh=0.9, best_cagr=0.10):
    return {
        "SPY": Benchmark(sharpe=spy, cagr=0.08, max_drawdown=0.33),
        "QQQ": Benchmark(sharpe=qqq, cagr=best_cagr, max_drawdown=0.35),
        "SMH": Benchmark(sharpe=smh, cagr=best_cagr, max_drawdown=0.55),
    }


def test_gate_all_pass():
    g = evaluate_gate(1.2, 0.10, 0.20, _benchmarks(qqq=0.8, smh=0.9, best_cagr=0.10))
    assert g.sharpe_pass is True
    assert g.beats_benchmarks is True  # 1.2 > max(0.8,0.9)
    assert g.abs_return_ok is True  # CAGR 0.20 >= 0.10*0.5
    assert g.mdd_hard_pass is True
    assert g.overall_pass is True


def test_gate_fails_when_sharpe_low():
    g = evaluate_gate(0.5, 0.10, 0.20, _benchmarks(qqq=0.3, smh=0.3))
    assert g.sharpe_pass is False
    assert g.overall_pass is False


def test_gate_fails_when_loses_to_qqq_smh():
    # 전략 1.0 < SMH 1.5 → QQQ/SMH 대비 우위 실패(SPY만 봤다면 통과했을 것).
    g = evaluate_gate(1.0, 0.10, 0.20, _benchmarks(spy=0.5, qqq=0.8, smh=1.5))
    assert g.beats_benchmarks is False
    assert g.overall_pass is False


def test_gate_fails_when_absolute_return_far_behind():
    # 전략 CAGR 0.03 < 최고 벤치마크 CAGR 0.25 × 0.5 = 0.125 → 절대수익 미달.
    g = evaluate_gate(1.5, 0.10, 0.03, _benchmarks(qqq=0.4, smh=0.4, best_cagr=0.25))
    assert g.abs_return_ok is False
    assert g.overall_pass is False


def test_gate_fails_when_mdd_breaches_hard():
    g = evaluate_gate(1.5, 0.25, 0.20, _benchmarks(qqq=0.3, smh=0.3))
    assert g.mdd_hard_pass is False
    assert g.overall_pass is False


def test_gate_design_mdd_separate_from_hard():
    g = evaluate_gate(1.2, 0.18, 0.20, _benchmarks(qqq=0.3, smh=0.3))
    assert g.mdd_design_pass is False
    assert g.mdd_hard_pass is True


# --- calibrate_fraction (순수, step10 양방향) ---


def test_calibrate_suggests_lower_when_mdd_high():
    c = calibrate_fraction(0.5, 0.18, mdd_target=0.15)  # 18% > 15%
    assert c.suggested_fraction < 0.5


def test_calibrate_suggests_higher_when_mdd_low():
    c = calibrate_fraction(0.01, 0.098, mdd_target=0.15)  # 9.8% < 하한 12% → 예산 미사용
    assert c.suggested_fraction > 0.01


def test_calibrate_keeps_within_band():
    c = calibrate_fraction(0.5, 0.13, mdd_target=0.15)  # 13% ∈ [12%,15%]
    assert c.suggested_fraction == 0.5


def test_calibrate_zero_mdd_keeps_fraction():
    c = calibrate_fraction(0.5, 0.0)
    assert c.suggested_fraction == 0.5


# --- run_v1 (mock, 네트워크 없음) ---


def test_run_v1_returns_report():
    report = run_v1(_provider(), ["AAA"])
    assert isinstance(report, V1Report)
    assert report.strategy.total_trades >= 0


def test_run_v1_includes_spy_benchmark():
    report = run_v1(_provider(), ["AAA"])
    assert hasattr(report.strategy.benchmark, "sharpe")


def test_run_v1_exit_layer_ab_has_multiple_configs():
    report = run_v1(_provider(), ["AAA"])
    names = [ab.name for ab in report.exit_layer_ab]
    assert len(report.exit_layer_ab) >= 3  # baseline + 단계별
    assert any("baseline" in n.lower() or "①" in n for n in names)


def test_run_v1_survivorship_warning_present():
    report = run_v1(_provider(), ["AAA"])
    assert isinstance(report.survivorship_warning, str)
    assert report.survivorship_warning


def test_run_v1_gate_checklist_present():
    report = run_v1(_provider(), ["AAA"])
    assert isinstance(report.gate.overall_pass, bool)


# --- step8: 다중 벤치마크 + 노출도 ---


def test_v1_report_has_multi_benchmarks():
    report = run_v1(_provider(), ["AAA"])
    assert set(report.strategy.benchmarks) >= {"SPY", "QQQ", "SMH"}


def test_v1_report_has_exposure():
    report = run_v1(_provider(), ["AAA"])
    assert 0.0 <= report.strategy.time_in_market_pct <= 1.0


def test_format_report_shows_benchmarks_and_exposure():
    text = format_report(run_v1(_provider(), ["AAA"]))
    assert "QQQ" in text and "SMH" in text
    assert "노출" in text or "time" in text.lower()


# --- step10: 게이트가 QQQ/SMH 기준 + 공격성(max_risk_pct) ---


def test_gate_uses_qqq_smh_not_spy_only():
    report = run_v1(_provider(), ["AAA"])
    # 게이트는 QQQ/SMH 중 강한 쪽과 비교(SPY 단독 아님).
    competitors = max(
        report.strategy.benchmarks["QQQ"].sharpe,
        report.strategy.benchmarks["SMH"].sharpe,
    )
    assert report.gate.toughest_benchmark_sharpe == competitors


def test_higher_risk_pct_raises_mdd_and_return_within_ceiling():
    from algorithms.backtest import BacktestParams, run_backtest

    prov = _provider()
    price_data = {"AAA": prov.get_ohlcv("AAA")}
    spy = prov.get_ohlcv("SPY")
    vix = prov.get_vix()
    # 자본 충분(affordability 비제약) → 공격성만의 효과 측정.
    low = run_backtest(
        price_data, spy, vix,
        params=BacktestParams(max_risk_pct=0.01, initial_capital=1_000_000),
    )
    high = run_backtest(
        price_data, spy, vix,
        params=BacktestParams(max_risk_pct=0.02, initial_capital=1_000_000),
    )
    # 공격성↑ → MDD·총수익 단조 증가(또는 동일), 단 20% 천장 안.
    assert high.max_drawdown >= low.max_drawdown - 1e-9
    assert high.total_return >= low.total_return - 1e-9
    assert high.max_drawdown <= 0.20


# --- format_report ---


def test_format_report_contains_key_sections():
    report = run_v1(_provider(), ["AAA"])
    text = format_report(report)
    assert "생존편향" in text
    assert "SPY" in text
    assert "GO/NO-GO" in text or "go/no-go" in text.lower()


# --- 안전: 자동 라이브 진입 코드 없음 ---


def test_no_live_order_symbols_in_module():
    import agents.v1_run as mod
    src = __import__("inspect").getsource(mod)
    # 실주문/실거래 API 호출이 이 연구 모듈에 없어야 한다.
    for forbidden in ("place_order", "submit_order", "robinhood", "execute_order"):
        assert forbidden not in src.lower()
