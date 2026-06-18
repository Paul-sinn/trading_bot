"""Step 2 scanner-agent 테스트 (TDD Red→Green).

spec: specs/scanner_agent.md
- scan(): BULLISH & 필터 통과 심볼만 후보로. 약세/탈락 심볼 제외.
- 빈 워치리스트 → 빈 리스트.
- 한 심볼 provider 예외 → 그 심볼만 제외, 나머지는 정상 스캔(격리).
- registry.killed=True → scan 스킵(빈 리스트), tick은 latest_candidates 비움.
- CRITICAL(ADR-002): 지표 재구현 없이 algorithms 순수 함수만 호출.
"""

import asyncio

import numpy as np
import pandas as pd

from agents.base import AgentRegistry
from agents.scanner import (
    Candidate,
    MockPriceDataProvider,
    ScannerAgent,
)
from algorithms.filters import MockSentimentProvider


# --- 결정론적 합성 OHLCV 헬퍼 ---


def _make_df(prices) -> pd.DataFrame:
    prices = np.array(prices, dtype=float)
    vol = np.full(len(prices), 1000.0)
    vol[-1] = 5000.0  # 마지막 봉 거래량 급등(Layer2 volume_spike 통과)
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": vol,
        }
    )


def _bullish_df() -> pd.DataFrame:
    # phase5 §1 재설계: 명백한 중기 상승추세(종가>50d MA>200d MA) → trend UP → overall BULLISH.
    # (옛 "하락 후 RSI 과매도 반등" 패러다임은 헌장 §1이 폐기 — 그건 추세 DOWN이다.)
    return _make_df(np.linspace(80, 200, 260))


def _bearish_df() -> pd.DataFrame:
    # 명백한 중기 하락추세(종가<50d MA<200d MA) → trend DOWN → overall BEARISH → 후보 제외.
    return _make_df(np.linspace(200, 80, 260))


WATCH = ["BULL", "BEAR"]


def _provider() -> MockPriceDataProvider:
    return MockPriceDataProvider({"BULL": _bullish_df(), "BEAR": _bearish_df()})


def _agent(registry=None, provider=None, watchlist=None, sentiment=None):
    return ScannerAgent(
        registry or AgentRegistry(),
        provider or _provider(),
        watchlist if watchlist is not None else list(WATCH),
        vix_provider=None,
        sentiment_provider=sentiment or MockSentimentProvider(),
    )


# --- scan() 후보 판정 ---


def test_scan_includes_bullish_symbol():
    agent = _agent()
    candidates = asyncio.run(agent.scan())
    symbols = [c.symbol for c in candidates]
    assert "BULL" in symbols
    bull = next(c for c in candidates if c.symbol == "BULL")
    assert isinstance(bull, Candidate)
    assert bull.filters_passed is True


def test_scan_excludes_bearish_symbol():
    agent = _agent()
    candidates = asyncio.run(agent.scan())
    assert "BEAR" not in [c.symbol for c in candidates]


def test_scan_excludes_when_filter_fails():
    # BULLISH 시그널이지만 센티먼트 부정 → 필터 탈락 → 후보 제외.
    agent = _agent(sentiment=MockSentimentProvider({"BULL": False}))
    candidates = asyncio.run(agent.scan())
    assert "BULL" not in [c.symbol for c in candidates]


def test_scan_empty_watchlist_returns_empty():
    agent = _agent(watchlist=[])
    assert asyncio.run(agent.scan()) == []


def test_scan_one_symbol_exception_does_not_block_others():
    # MISSING 심볼은 provider가 KeyError → 그 심볼만 제외, BULL은 정상 후보.
    agent = _agent(watchlist=["MISSING", "BULL"])
    candidates = asyncio.run(agent.scan())
    assert "BULL" in [c.symbol for c in candidates]


def test_scan_skipped_when_registry_killed():
    registry = AgentRegistry()
    registry.kill_all("리스크 한도 초과")
    agent = _agent(registry=registry)
    assert asyncio.run(agent.scan()) == []


# --- tick() ---


def test_tick_stores_latest_candidates():
    agent = _agent()
    asyncio.run(agent.tick())
    assert "BULL" in [c.symbol for c in agent.latest_candidates]


def test_tick_when_killed_clears_candidates():
    registry = AgentRegistry()
    agent = _agent(registry=registry)
    asyncio.run(agent.tick())
    assert agent.latest_candidates  # 살아있을 때는 후보 존재
    registry.kill_all("정지")
    asyncio.run(agent.tick())
    assert agent.latest_candidates == []
