"""Phase 5 step11 — 생존편향 없는 약세장 OOS 재검증 러너 테스트 (TDD Red→Green).

spec: specs/oos_validate.md  ·  헌장: §3/§10②
- 네트워크 없이 MockPointInTimeProvider로. point-in-time 유니버스(상폐종목 포함)·약세장 윈도우.
- working fraction 보수적(0.015). 자동 라이브 진입 없음.
"""

import numpy as np
import pandas as pd

from agents.data_adapter import MockPointInTimeProvider
from agents.oos_validate import format_oos_report, run_oos_validation
from agents.v1_run import V1Report
from algorithms.universe import SymbolMetrics


def _ohlcv(close: np.ndarray, start="2015-01-01") -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range(start, periods=n, freq="D")
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pd.DataFrame(
        {
            "open": open_, "high": np.maximum(open_, close) * 1.003,
            "low": np.minimum(open_, close) * 0.997, "close": close,
            "volume": np.full(n, 1e6),
        },
        index=idx,
    )


def _uptrend_pullbacks(n: int) -> np.ndarray:
    out = [80.0]
    up, c = True, 0
    for _ in range(1, n):
        out.append(out[-1] + (1.0 if up else -1.5))
        c += 1
        if up and c >= 15:
            up, c = False, 0
        elif not up and c >= 6:
            up, c = True, 0
    return np.array(out)


def _provider() -> MockPointInTimeProvider:
    n = 600  # ~2015~2017+ 충분히 긴 일봉
    idx = pd.date_range("2015-01-01", periods=n, freq="D")
    frames = {
        "AAA": _ohlcv(_uptrend_pullbacks(n)),
        "DEAD": _ohlcv(_uptrend_pullbacks(n)),  # 나중에 상폐될 종목(데이터는 존재)
        "SPY": _ohlcv(np.linspace(100, 130, n)),
        "QQQ": _ohlcv(np.linspace(100, 150, n)),
        "SMH": _ohlcv(np.linspace(100, 180, n)),
    }
    metrics = {
        "AAA": SymbolMetrics("2010-01-01", None, 1e8, 0.03, False),
        "DEAD": SymbolMetrics("2010-01-01", "2016-06-01", 1e8, 0.03, False),  # 2016 상폐
    }
    vix = pd.Series(np.full(n, 15.0), index=idx)
    return MockPointInTimeProvider(frames, metrics, vix)


def test_run_oos_returns_report_per_window():
    windows = {
        "early": ("2015-03-01", "2016-03-01"),
        "late": ("2016-09-01", "2017-06-01"),
    }
    results = run_oos_validation(_provider(), windows)
    assert set(results) <= set(windows)
    for rep in results.values():
        assert isinstance(rep, V1Report)


def test_point_in_time_universe_includes_delisted_before_delisting():
    prov = _provider()
    # 2016-06-01 상폐 전 → DEAD 포함(생존편향 제거).
    assert "DEAD" in prov.get_constituents("2015-03-01")
    # 상폐 후 → 제외.
    assert "DEAD" not in prov.get_constituents("2017-01-01")


def test_oos_uses_conservative_fraction():
    # working fraction 보수적(0.015) 기본 — MDD가 20% 천장 안.
    windows = {"w": ("2015-03-01", "2016-12-01")}
    results = run_oos_validation(_provider(), windows)
    for rep in results.values():
        assert rep.strategy.max_drawdown <= 0.20


def test_format_oos_report_mentions_survivorship_and_qqq():
    windows = {"w": ("2015-03-01", "2016-12-01")}
    text = format_oos_report(run_oos_validation(_provider(), windows))
    assert "생존편향" in text
    assert "QQQ" in text
    assert "GO/NO-GO" in text or "go/no-go" in text.lower()


def test_no_live_order_code_in_module():
    import agents.oos_validate as mod

    src = __import__("inspect").getsource(mod)
    for forbidden in ("place_order", "submit_order", "robinhood", "execute_order"):
        assert forbidden not in src.lower()
