"""다일 성과 리포트 — 시뮬 스냅샷 + 매매로그에서 성과 지표를 산출한다(순수 측정).

상태를 바꾸지 않고 기존 산출물(snapshots, trade_log)만 읽는다. 실브로커/Robinhood/MCP/라이브 주문
없음 — real_orders_placed는 항상 0. LLM/이벤트 캘린더 실연동 없음. 전략 시그널 변경 없음. 측정만.

spec: specs/perf_report.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.multiday import MultiDayResult
    from agents.sim_portfolio import PortfolioSnapshot, TradeRecord


@dataclass(frozen=True)
class PerformanceReport:
    """다일 성과 지표(측정 보조 — 판단 아님)."""

    starting_cash: float
    equity_curve: tuple[float, ...]
    cumulative_return: float
    max_drawdown: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    win_rate: float
    average_win: float
    average_loss: float
    num_trades: int
    num_closed_trades: int
    exposure_over_time: tuple[float, ...]

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음."""
        return 0


def _max_drawdown(curve: tuple[float, ...]) -> float:
    """equity 곡선의 최대 고점→저점 하락률(분수). 빈 곡선/0 이하 peak는 0."""
    peak = float("-inf")
    mdd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def compute_performance(
    snapshots: tuple["PortfolioSnapshot", ...],
    trade_log: tuple["TradeRecord", ...],
    *,
    starting_cash: float,
) -> PerformanceReport:
    """스냅샷 + 매매로그에서 성과 지표를 산출한다(순수). 빈 입력도 안전하게 0으로."""
    equity_curve = tuple(s.equity for s in snapshots)
    exposure_over_time = tuple(s.total_exposure for s in snapshots)

    if equity_curve and starting_cash > 0:
        cumulative_return = (equity_curve[-1] - starting_cash) / starting_cash
    else:
        cumulative_return = 0.0

    max_drawdown = _max_drawdown(equity_curve)
    unrealized_pnl = snapshots[-1].unrealized_pnl if snapshots else 0.0

    sells = [t for t in trade_log if t.side == "sell"]
    realized_pnl = sum(t.realized_pnl for t in sells)
    wins = [t.realized_pnl for t in sells if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in sells if t.realized_pnl < 0]

    num_closed = len(sells)
    win_rate = (len(wins) / num_closed) if num_closed > 0 else 0.0
    average_win = (sum(wins) / len(wins)) if wins else 0.0
    average_loss = (sum(losses) / len(losses)) if losses else 0.0

    return PerformanceReport(
        starting_cash=starting_cash,
        equity_curve=equity_curve,
        cumulative_return=cumulative_return,
        max_drawdown=max_drawdown,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_pnl=realized_pnl + unrealized_pnl,
        win_rate=win_rate,
        average_win=average_win,
        average_loss=average_loss,
        num_trades=len(trade_log),
        num_closed_trades=num_closed,
        exposure_over_time=exposure_over_time,
    )


def performance_from_multiday(result: "MultiDayResult") -> PerformanceReport:
    """MultiDayResult에서 스냅샷·매매로그·starting_cash를 추출해 성과를 계산한다."""
    snapshots = tuple(s for s in result.daily_snapshots if s is not None)
    return compute_performance(
        snapshots,
        result.portfolio.trade_log,
        starting_cash=result.portfolio.starting_cash,
    )


def format_performance_report(report: PerformanceReport) -> str:
    """사람이 읽는 성과 텍스트(측정 보조, 판단 아님)."""
    lines = []
    lines.append("=" * 60)
    lines.append("Multi-Day Simulated Performance (측정 — 실주문 없음)")
    lines.append("=" * 60)
    lines.append(f"  starting_cash      : {report.starting_cash:.2f}")
    lines.append(f"  cumulative_return  : {report.cumulative_return:.2%}")
    lines.append(f"  max_drawdown       : {report.max_drawdown:.2%}")
    lines.append(f"  realized_pnl       : {report.realized_pnl:.2f}")
    lines.append(f"  unrealized_pnl     : {report.unrealized_pnl:.2f}")
    lines.append(f"  total_pnl          : {report.total_pnl:.2f}")
    lines.append(
        f"  win_rate           : {report.win_rate:.2%}  "
        f"(closed {report.num_closed_trades} / trades {report.num_trades})"
    )
    lines.append(f"  average_win        : {report.average_win:.2f}")
    lines.append(f"  average_loss       : {report.average_loss:.2f}")
    lines.append(f"  equity_curve points: {len(report.equity_curve)}")
    lines.append(f"  real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 60)
    return "\n".join(lines)
