"""백테스트 데이터 → 멀티데이 시뮬 구동 — 과거 일봉으로 dry-run 루프를 돌린다.

일별 point-in-time 슬라이스(미래참조 없음)로 기존 구조(ScannerAgent/MockPriceDataProvider/evidence/
multiday/perf_report)를 재사용해 구동한다. 새 전략/시그널 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 튜닝 없음.

CRITICAL(미래참조 금지): day D에는 D까지의 데이터만 본다(df.loc[:D]). 이력 부족 심볼은 후보가 안 된다.
CRITICAL(fail-closed): 데이터 결측은 후보 미생성·data_missing·사이징 무효로 거래를 막는다.

spec: specs/historical_sim.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agents.base import AgentRegistry
from agents.evidence import EvidenceParams, EventRiskProvider, build_contexts
from agents.multiday import DayInput, MultiDayResult, run_phase1_multiday
from agents.perf_report import PerformanceReport, performance_from_multiday
from agents.scanner import MockPriceDataProvider, ScannerAgent
from agents.sim_exit import ExitParams
from algorithms.policy import Policy
from agents.sim_portfolio import SimulatedPortfolio, TradeRecord


@dataclass(frozen=True)
class HistoricalResult:
    """과거 데이터 구동 결과 — 멀티데이 + 성과. real_orders_placed는 항상 0."""

    multiday: MultiDayResult
    performance: PerformanceReport

    @property
    def real_orders_placed(self) -> int:
        return 0

    @property
    def portfolio(self) -> SimulatedPortfolio:
        return self.multiday.portfolio

    @property
    def trade_log(self) -> tuple[TradeRecord, ...]:
        return self.multiday.portfolio.trade_log


def _slice(series, as_of):
    """series/df를 as_of까지 슬라이스(미래참조 금지). 비-pandas(스칼라)는 그대로."""
    if hasattr(series, "loc"):
        return series.loc[:as_of]
    return series


async def _build_day(
    as_of,
    price_data: dict[str, pd.DataFrame],
    spy_prices,
    vix,
    benchmark_prices,
    params: EvidenceParams,
    event_provider,
    default_exit_params: ExitParams | None,
) -> DayInput:
    """day D의 DayInput을 point-in-time 슬라이스로 구성한다."""
    frames: dict[str, pd.DataFrame] = {}
    mark_prices: dict[str, float] = {}
    for sym, df in price_data.items():
        sliced = df.loc[:as_of]
        if len(sliced) == 0:
            continue  # 상장 전 — 후보 없음.
        frames[sym] = sliced
        if as_of in df.index:
            mark_prices[sym] = float(df.loc[as_of, "close"])

    scanner = ScannerAgent(AgentRegistry(), MockPriceDataProvider(frames), list(frames))
    candidates = await scanner.scan()
    contexts = await build_contexts(
        candidates,
        scanner.price_provider,
        spy_prices=_slice(spy_prices, as_of),
        vix=_slice(vix, as_of),
        params=params,
        benchmark_prices=_slice(benchmark_prices, as_of) if benchmark_prices is not None else None,
        event_provider=event_provider,
    )

    exits: dict[str, ExitParams] = {}
    if default_exit_params is not None:
        exits = {sym: default_exit_params for sym in frames}  # 미보유 심볼은 apply_exit가 무시.

    date_str = str(as_of.date()) if hasattr(as_of, "date") else str(as_of)
    return DayInput(
        date=date_str, scanner=scanner, contexts=contexts,
        mark_prices=mark_prices, exits=exits,
    )


async def run_historical_simulation(
    *,
    price_data: dict[str, pd.DataFrame],
    spy_prices,
    vix,
    policy: Policy,
    account_cash: float,
    benchmark_prices=None,
    trading_days=None,
    params: EvidenceParams | None = None,
    event_provider: EventRiskProvider | None = None,
    warmup: int = 200,
    default_exit_params: ExitParams | None = None,
) -> HistoricalResult:
    """과거 일봉으로 멀티데이 dry-run을 구동하고 성과를 산출한다(실주문 0)."""
    if params is None:
        params = EvidenceParams(account_equity=account_cash)
    if trading_days is None:
        idx = spy_prices.index if hasattr(spy_prices, "index") else []
        trading_days = list(idx[warmup:])

    days = [
        await _build_day(
            as_of, price_data, spy_prices, vix, benchmark_prices,
            params, event_provider, default_exit_params,
        )
        for as_of in trading_days
    ]

    multiday = await run_phase1_multiday(days=days, policy=policy, account_cash=account_cash)
    performance = performance_from_multiday(multiday)
    return HistoricalResult(multiday=multiday, performance=performance)
