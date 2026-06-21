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

from dataclasses import dataclass, replace

import pandas as pd

from agents.base import AgentRegistry
from agents.evidence import EvidenceParams, EventRiskProvider, build_contexts
from agents.multiday import DayInput, MultiDayResult, run_phase1_multiday
from agents.perf_report import PerformanceReport, performance_from_multiday
from agents.scanner import MockPriceDataProvider, ScannerAgent
from agents.sim_exit import ExitParams, ExitPolicy
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


# 진입 체결 모델(opt-in). current는 기존 동작(시그널일 close 즉시 체결).
ENTRY_FILL_MODELS = ("current", "next-bar-limit", "next-open")


def resolve_entry_fill(reference_price, next_bar, model: str, buffer: float):
    """진입 체결가를 결정한다(순수). 미체결이면 None.

    current: reference_price 그대로(다음 바 무관).
    next-bar-limit: limit=ref×(1+buffer). next_open≤limit→next_open, 아니면 next_low≤limit→limit, 그 외 미체결.
    next-open: 다음 바 있으면 next_open, 없으면 미체결.
    next_bar는 (open, high, low) 또는 None.
    """
    if model == "current":
        return reference_price
    if next_bar is None:
        return None
    nopen, _nhigh, nlow = next_bar
    if model == "next-open":
        return nopen
    # next-bar-limit
    limit = reference_price * (1.0 + buffer)
    if nopen <= limit:
        return nopen
    if nlow <= limit:
        return limit
    return None


def _next_bar_after(df, as_of):
    """as_of 다음 거래 바 (open, high, low). 결측/마지막 바면 None."""
    if df is None or as_of not in df.index:
        return None
    pos = df.index.get_loc(as_of)
    if not isinstance(pos, int) or pos + 1 >= len(df.index):
        return None
    row = df.iloc[pos + 1]
    try:
        return float(row["open"]), float(row["high"]), float(row["low"])
    except (KeyError, TypeError, ValueError):
        return None


def _apply_entry_fill_model(contexts, price_data, as_of, model: str, buffer: float):
    """후보 컨텍스트에 진입 체결 모델을 적용한다(reference_price 교체 또는 미체결 시 quantity=0).

    사이징(quantity)·veto·스캐너/디시전은 바꾸지 않는다 — 미체결은 quantity=0으로 기존 RiskGate가
    veto하게 둬 주문/체결/포지션이 생기지 않는다(우회 없음). model=current면 그대로 반환.
    """
    if model == "current":
        return contexts
    adjusted: dict = {}
    for sym, ctx in contexts.items():
        if ctx.reference_price <= 0:
            adjusted[sym] = ctx       # 가격 없음 — 어차피 체결 컨텍스트 미생성.
            continue
        next_bar = _next_bar_after(price_data.get(sym), as_of)
        fill_price = resolve_entry_fill(ctx.reference_price, next_bar, model, buffer)
        if fill_price is None:
            adjusted[sym] = replace(ctx, quantity=0)             # 미체결 → veto → no trade.
        else:
            adjusted[sym] = replace(ctx, reference_price=fill_price)
    return adjusted


async def _build_day(
    as_of,
    price_data: dict[str, pd.DataFrame],
    spy_prices,
    vix,
    benchmark_prices,
    params: EvidenceParams,
    event_provider,
    default_exit_params: ExitParams | None,
    entry_fill_model: str = "current",
    entry_limit_buffer_pct: float = 0.03,
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
    # opt-in: 진입 체결을 다음 거래 바로 모델링(기본 current면 무변경).
    contexts = _apply_entry_fill_model(
        contexts, price_data, as_of, entry_fill_model, entry_limit_buffer_pct
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
    exit_policy: ExitPolicy | None = None,
    entry_fill_model: str = "current",
    entry_limit_buffer_pct: float = 0.03,
) -> HistoricalResult:
    """과거 일봉으로 멀티데이 dry-run을 구동하고 성과를 산출한다(실주문 0).

    exit_policy(활성)가 주어지면 매일 보유 포지션의 진입가/보유일로 청산을 동적 평가한다(stop/trailing/
    time/manual). 없으면 청산 미적용 — 포지션은 OPEN으로 남는다(기본 동작 불변).
    """
    if params is None:
        params = EvidenceParams(account_equity=account_cash)
    if trading_days is None:
        idx = spy_prices.index if hasattr(spy_prices, "index") else []
        trading_days = list(idx[warmup:])

    # exit_policy를 쓰면 정적 default_exit_params는 무시(이중 청산 방지) — 동적 경로가 단일 진실.
    static_exits = None if (exit_policy is not None and exit_policy.is_active) else default_exit_params

    days = [
        await _build_day(
            as_of, price_data, spy_prices, vix, benchmark_prices,
            params, event_provider, static_exits,
            entry_fill_model=entry_fill_model,
            entry_limit_buffer_pct=entry_limit_buffer_pct,
        )
        for as_of in trading_days
    ]

    multiday = await run_phase1_multiday(
        days=days, policy=policy, account_cash=account_cash, exit_policy=exit_policy
    )
    performance = performance_from_multiday(multiday)
    return HistoricalResult(multiday=multiday, performance=performance)
