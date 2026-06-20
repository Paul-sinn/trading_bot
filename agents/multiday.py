"""다일 시뮬레이션 루프 — Phase 1 흐름을 여러 거래일에 돌리며 같은 포트폴리오를 이월한다.

일별로 run_phase1_dry_run을 동일 SimulatedPortfolio로 재사용한다(중복 로직 없음). 포지션·현금·노출·
실현PnL·매매로그가 날을 넘겨 누적된다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음.

spec: specs/multiday.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.decision import MockDecisionProvider
from agents.phase1_flow import CandidateContext, Phase1Result, run_phase1_dry_run
from agents.sim_exit import (
    ExitParams,
    ExitPolicy,
    ExitResult,
    apply_exit,
    exit_params_for_position,
)
from agents.sim_portfolio import PortfolioSnapshot, SimulatedPortfolio, TradeRecord


@dataclass(frozen=True)
class DayInput:
    """하루치 입력. scanner는 async .scan()을 갖는 객체."""

    date: str
    scanner: object
    contexts: dict[str, CandidateContext]
    regime_name: str = "NORMAL_BULL"
    compass_state: str = "strong"
    account_phase: str = "1"
    risk_mode_name: str = "B"
    decision_provider: object | None = None
    mark_prices: dict[str, float] | None = None  # 그날 종가(mark-to-market). 없으면 cost_basis + data_missing.
    exits: dict[str, ExitParams] = field(default_factory=dict)  # 그날 청산 평가할 보유 심볼별 파라미터.


@dataclass(frozen=True)
class MultiDayResult:
    """다일 결과. day_results는 일별, portfolio는 최종 누적."""

    day_results: tuple[Phase1Result, ...]
    portfolio: SimulatedPortfolio
    day_exits: tuple[tuple[ExitResult, ...], ...] = ()

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음."""
        return 0

    @property
    def daily_snapshots(self) -> tuple[PortfolioSnapshot | None, ...]:
        """일별 종료 시점 누적 스냅샷."""
        return tuple(r.report.portfolio_snapshot for r in self.day_results)

    @property
    def trade_log(self) -> tuple[TradeRecord, ...]:
        """전 기간 누적 매매로그."""
        return self.portfolio.trade_log


async def run_phase1_multiday(
    *,
    days: list[DayInput],
    policy,
    account_cash: float | None = None,
    portfolio: SimulatedPortfolio | None = None,
    exit_policy: ExitPolicy | None = None,
) -> MultiDayResult:
    """Phase 1 흐름을 일별로 돌리며 동일 포트폴리오를 이월한다(실주문 0).

    exit_policy가 주어지면(활성) 매일 현재 보유 포지션의 진입가/보유일로 포지션별 ExitParams를 만들어
    청산을 평가한다(stop/trailing/time/manual). 없으면 기존처럼 DayInput.exits만 쓴다 — 기본 동작 불변.
    """
    if portfolio is None:
        portfolio = SimulatedPortfolio(account_cash if account_cash is not None else 0.0)

    use_policy = exit_policy is not None and exit_policy.is_active
    hold_days: dict[str, int] = {}  # 심볼별 보유 일수(청산 전 증가).

    day_results: list[Phase1Result] = []
    day_exits: list[tuple[ExitResult, ...]] = []
    for day in days:
        # 1) 그날 종가로 trailing_high 갱신(상승 시만) → 트레일링 스탑이 갱신된 고점을 쓴다.
        prices = day.mark_prices or {}
        portfolio.update_trailing_highs(prices)

        # 2) 청산 평가·적용(entry 전 — 청산이 현금을 풀어 그날 신규 진입에 쓰일 수 있게).
        if use_policy:
            for sym in portfolio.positions:  # 보유 포지션 보유일 +1(이번 날 포함).
                hold_days[sym] = hold_days.get(sym, 0) + 1
            exit_params_by_sym = {
                sym: exit_params_for_position(
                    exit_policy,
                    avg_entry_price=pos.avg_entry_price,
                    hold_days=hold_days.get(sym, 0),
                    manual=(exit_policy.manual_exit_date is not None
                            and day.date == exit_policy.manual_exit_date),
                )
                for sym, pos in portfolio.positions.items()
            }
        else:
            exit_params_by_sym = day.exits

        exit_results = tuple(
            apply_exit(portfolio, sym, price=prices.get(sym), params=params)
            for sym, params in exit_params_by_sym.items()
        )
        if use_policy:  # 청산된 심볼은 보유일 추적 정리(재진입 시 새로 카운트).
            for sym in list(hold_days):
                if sym not in portfolio.positions:
                    hold_days.pop(sym, None)
        day_exits.append(exit_results)

        provider = day.decision_provider or MockDecisionProvider()
        res = await run_phase1_dry_run(
            scanner=day.scanner,
            decision_provider=provider,
            policy=policy,
            account_phase=day.account_phase,
            risk_mode_name=day.risk_mode_name,
            regime_name=day.regime_name,
            compass_state=day.compass_state,
            contexts=day.contexts,
            report_date=day.date,
            portfolio=portfolio,  # 동일 포트폴리오 이월 — 다음 날이 갱신된 상태를 본다.
            mark_prices=day.mark_prices,  # 그날 종가로 mark-to-market.
        )
        day_results.append(res)

    return MultiDayResult(tuple(day_results), portfolio, tuple(day_exits))
