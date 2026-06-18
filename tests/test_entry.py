"""Phase 5 step3 — 눌림목/돌파 진입 판정 테스트 (TDD Red→Green).

spec: specs/entry.md  ·  헌장: docs/STRATEGY.md §1/§8
- 순수 함수: (df, regime, spy_df) → EntrySignal. 외부 I/O 없음.
- 게이트(추세 UP + 상대강도 + 레짐 allow) AND 트리거(눌림 후 재개 / 돌파).
- step0(signals)·step1(regime) 함수를 호출(재구현 금지).
"""

import numpy as np
import pandas as pd

from algorithms.entry import EntrySignal, breakout_entry, pullback_entry
from algorithms.regime import Regime


def _df(close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )


def _weak_spy(n: int) -> pd.DataFrame:
    # 자산보다 약한 벤치마크(상대강도 True 유도).
    return _df(np.linspace(100, 108, n))


def _strong_spy(n: int) -> pd.DataFrame:
    # 자산보다 강한 벤치마크(상대강도 False 유도).
    return _df(np.linspace(100, 300, n))


def _uptrend_with_pullback() -> np.ndarray:
    # 강한 상승추세 후 20d선까지 눌림 → 마지막 2봉 재개(반등).
    base = np.linspace(80, 196, 252)
    pull = np.array([193.0, 188.0])   # 20d선 아래로 눌림
    resume = np.array([194.0, 199.0])  # 재개: 직전봉 초과 + 20d선 회복
    return np.concatenate([base, pull, resume])


def _uptrend_monotonic() -> np.ndarray:
    # 눌림 없는 단조 상승(돌파 트리거는 충족, 눌림 트리거는 미충족).
    return np.linspace(80, 200, 260)


def _downtrend() -> np.ndarray:
    return np.linspace(200, 80, 260)


# --- 눌림목: 정상 진입 ---


def test_pullback_enters_on_uptrend_pullback_resume_regime_a():
    close = _uptrend_with_pullback()
    df = _df(close)
    sig = pullback_entry(df, regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert isinstance(sig, EntrySignal)
    assert sig.enter is True


# --- 눌림목: 게이트 실패 ---


def test_pullback_blocked_when_trend_down():
    close = _downtrend()
    sig = pullback_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert sig.enter is False


def test_pullback_blocked_when_weaker_than_spy():
    close = _uptrend_with_pullback()
    sig = pullback_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_strong_spy(len(close)))
    assert sig.enter is False


def test_pullback_blocked_in_bearish_regime():
    close = _uptrend_with_pullback()
    sig = pullback_entry(_df(close), regime=Regime.BEARISH, spy_df=_weak_spy(len(close)))
    assert sig.enter is False


def test_pullback_blocked_in_panic_regime():
    close = _uptrend_with_pullback()
    sig = pullback_entry(_df(close), regime=Regime.PANIC, spy_df=_weak_spy(len(close)))
    assert sig.enter is False


# --- 눌림목: 트리거 미충족 ---


def test_pullback_no_trigger_on_monotonic_uptrend():
    # 게이트 통과(추세 UP+상대강도+레짐 A)지만 눌림이 없음 → 진입 안 함(눌림 대기).
    close = _uptrend_monotonic()
    sig = pullback_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert sig.enter is False


# --- 돌파(A/B 비교군) ---


def test_breakout_enters_on_new_high_uptrend():
    close = _uptrend_monotonic()  # 매 봉 신고가 → 돌파 트리거.
    sig = breakout_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert sig.enter is True


def test_breakout_blocked_in_cd_regime():
    close = _uptrend_monotonic()
    for regime in (Regime.BEARISH, Regime.PANIC):
        sig = breakout_entry(_df(close), regime=regime, spy_df=_weak_spy(len(close)))
        assert sig.enter is False


def test_breakout_no_new_high_does_not_enter():
    # 신고가 직후 하락 → 최신 종가가 직전 20봉 최고 미달 → 돌파 아님.
    close = np.concatenate([_uptrend_monotonic(), np.array([190.0, 185.0])])
    sig = breakout_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert sig.enter is False


# --- 엣지: 데이터 부족 ---


def test_insufficient_data_does_not_enter():
    close = np.linspace(80, 120, 50)  # 200d MA 계산 불가 → trend NEUTRAL → 게이트 실패.
    pb = pullback_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    bo = breakout_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert pb.enter is False
    assert bo.enter is False


def test_reason_is_populated():
    close = _downtrend()
    sig = pullback_entry(_df(close), regime=Regime.NORMAL_BULL, spy_df=_weak_spy(len(close)))
    assert isinstance(sig.reason, str) and sig.reason
