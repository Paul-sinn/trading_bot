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
    GateThresholds,
    V1Report,
    calibrate_fraction,
    evaluate_gate,
    format_report,
    run_v1,
)


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
    }
    vix = pd.Series(np.full(n, 15.0), index=idx)
    return MockDailyProvider(frames, vix=vix)


# --- evaluate_gate (순수 로직) ---


def test_gate_all_pass():
    g = evaluate_gate(sharpe=1.2, max_drawdown=0.10, benchmark_sharpe=0.8, thresholds=GateThresholds())
    assert g.sharpe_pass is True
    assert g.beats_spy_sharpe is True
    assert g.mdd_design_pass is True
    assert g.mdd_hard_pass is True
    assert g.overall_pass is True


def test_gate_fails_when_sharpe_low():
    g = evaluate_gate(sharpe=0.5, max_drawdown=0.10, benchmark_sharpe=0.3, thresholds=GateThresholds())
    assert g.sharpe_pass is False
    assert g.overall_pass is False


def test_gate_fails_when_loses_to_spy():
    g = evaluate_gate(sharpe=1.2, max_drawdown=0.10, benchmark_sharpe=1.5, thresholds=GateThresholds())
    assert g.beats_spy_sharpe is False
    assert g.overall_pass is False


def test_gate_fails_when_mdd_breaches_hard():
    g = evaluate_gate(sharpe=1.5, max_drawdown=0.25, benchmark_sharpe=0.5, thresholds=GateThresholds())
    assert g.mdd_hard_pass is False
    assert g.overall_pass is False


def test_gate_design_mdd_separate_from_hard():
    # 설계(15%) 초과지만 하드(20%) 이내 → design fail, hard pass.
    g = evaluate_gate(sharpe=1.2, max_drawdown=0.18, benchmark_sharpe=0.5, thresholds=GateThresholds())
    assert g.mdd_design_pass is False
    assert g.mdd_hard_pass is True


# --- calibrate_fraction (순수) ---


def test_calibrate_suggests_lower_fraction_when_mdd_high():
    c = calibrate_fraction(current_fraction=0.5, realized_mdd=0.30, mdd_target=0.15)
    assert c.suggested_fraction < 0.5  # MDD 2배 → fraction 축소 제안
    assert c.suggested_fraction == round(0.5 * 0.15 / 0.30, 10) or abs(c.suggested_fraction - 0.25) < 1e-9


def test_calibrate_keeps_fraction_when_mdd_within_target():
    c = calibrate_fraction(current_fraction=0.5, realized_mdd=0.10, mdd_target=0.15)
    assert c.suggested_fraction == 0.5  # 이미 목표 내 → 유지(더 키우지 않음)


def test_calibrate_zero_mdd_keeps_fraction():
    c = calibrate_fraction(current_fraction=0.5, realized_mdd=0.0, mdd_target=0.15)
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
