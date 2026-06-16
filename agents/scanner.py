"""스캐너 에이전트 — 워치리스트 순환 스캔 + 후보 리스트업.

spec: specs/scanner_agent.md

워치리스트를 순환하며 각 심볼의 가격 데이터를 받아 알고리즘 Layer 1(signals) + Layer 2(filters)를
적용해 매수 후보 종목 리스트를 만든다. Layer 3(sizing)과 Claude 판단은 후속 에이전트로 넘긴다.

원칙:
- ADR-002: 지표(EMA/RSI/MACD/ATR/거래량/VIX/센티먼트)를 재구현하지 않는다. algorithms의 순수
  함수를 호출만 한다(단일 진실). 이 클래스는 I/O(provider 조회)와 후보 조립만 담당한다.
- ADR-001/002: 외부(Robinhood/Claude) API를 직접 호출하지 않는다. 가격·VIX·센티먼트는 모두
  주입된 provider(Mock)로만 들어온다.
- 격리: 한 심볼의 예외가 전체 스캔(1분 루프)을 막지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from agents.base import Agent, AgentRegistry
from algorithms import filters, signals
from algorithms.filters import MockSentimentProvider, SentimentProvider
from algorithms.signals import Signal, SignalResult

# VIX provider 미주입 시 사용하는 기본값(정상 범위 — vix_filter 통과).
_DEFAULT_VIX = 15.0


# --- Provider 인터페이스 (외부 의존 주입) ---


@runtime_checkable
class PriceDataProvider(Protocol):
    """OHLCV 가격 데이터 조회 인터페이스. 구현은 Mock/실거래(MCP)로 분기."""

    async def get_ohlcv(self, symbol: str) -> pd.DataFrame: ...


@runtime_checkable
class VixProvider(Protocol):
    """VIX 조회 인터페이스(선택 주입)."""

    async def get_vix(self) -> float | None: ...


class MockPriceDataProvider:
    """결정론적 합성 OHLCV provider (TDD용).

    생성 시 받은 심볼별 DataFrame 매핑으로 응답한다. 외부 호출·난수 없음.
    등록되지 않은 심볼은 KeyError(예외 격리 경로 검증용).
    """

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = dict(frames)

    async def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._frames:
            raise KeyError(f"가격 데이터 없음: {symbol!r}")
        return self._frames[symbol]


# --- 후보 모델 ---


@dataclass(frozen=True)
class Candidate:
    """Layer 1+2를 통과한 매수 후보."""

    symbol: str
    signal: SignalResult
    filters_passed: bool
    detail: dict


# --- 스캐너 에이전트 (상태 루프) ---


class ScannerAgent(Agent):
    """워치리스트를 순환 스캔해 후보 종목 리스트를 만든다.

    지표 계산은 algorithms 순수 함수에 위임하고, 이 클래스는 I/O(provider 조회)와 후보 조립만 한다.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        price_provider: PriceDataProvider,
        watchlist: list[str],
        *,
        vix_provider: VixProvider | None = None,
        sentiment_provider: SentimentProvider | None = None,
        name: str = "scanner",
    ) -> None:
        super().__init__(name)
        self.registry = registry
        self.price_provider = price_provider
        self.watchlist = list(watchlist)
        self.vix_provider = vix_provider
        self.sentiment_provider = sentiment_provider or MockSentimentProvider()
        self.latest_candidates: list[Candidate] = []

    async def scan(self) -> list[Candidate]:
        """워치리스트를 순환 평가해 후보 리스트를 반환한다.

        registry가 kill 상태면 즉시 빈 리스트(스캔 스킵). 한 심볼에서 예외가 나면 그 심볼만
        건너뛰고 나머지는 계속 스캔한다(격리 — 1분 루프가 한 종목 실패로 멈추지 않게).
        """
        if self.registry.is_killed():
            return []

        candidates: list[Candidate] = []
        for symbol in self.watchlist:
            try:
                candidate = await self._evaluate_symbol(symbol)
            except Exception:  # noqa: BLE001 — 한 심볼 실패가 전체 스캔을 막지 않게 격리.
                continue
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _evaluate_symbol(self, symbol: str) -> Candidate | None:
        """단일 심볼을 평가한다. BULLISH & 필터 통과면 Candidate, 아니면 None.

        algorithms 순수 함수(signals.generate_signals / filters.apply_filters)를 호출만 한다.
        """
        df = await self.price_provider.get_ohlcv(symbol)
        sig = signals.generate_signals(df)
        vix = await self._get_vix()
        filt = filters.apply_filters(df, symbol, vix, self.sentiment_provider)

        if sig.overall != Signal.BULLISH or not filt.passed:
            return None

        return Candidate(
            symbol=symbol,
            signal=sig,
            filters_passed=filt.passed,
            detail={"signal": sig, "filter": filt, "vix": vix},
        )

    async def _get_vix(self) -> float | None:
        """주입된 vix_provider가 있으면 조회, 없으면 기본 정상값."""
        if self.vix_provider is None:
            return _DEFAULT_VIX
        return await self.vix_provider.get_vix()

    async def tick(self) -> None:
        """루프 1회: scan() 결과를 latest_candidates에 저장(killed면 빈 결과)."""
        self.latest_candidates = await self.scan()
