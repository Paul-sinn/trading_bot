# SPEC: perf_report (다일 성과 리포트)

다일 시뮬 포트폴리오의 일별 스냅샷 + 매매로그에서 성과 지표를 산출한다. 순수 분석 — 상태를 바꾸지 않고
기존 산출물(snapshots, trade_log)만 읽는다.

관련: `agents/multiday.py`(MultiDayResult), `agents/sim_portfolio.py`(PortfolioSnapshot, TradeRecord).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음. 측정만 — 튜닝/판단 없음.

## 지표 (PerformanceReport)
- `equity_curve`: 일별 equity(스냅샷에서)
- `cumulative_return`: (최종 equity − starting_cash) / starting_cash
- `max_drawdown`: equity 곡선의 최대 고점→저점 하락률(분수)
- `realized_pnl`: 매도(closed) 실현손익 합
- `unrealized_pnl`: 최종 스냅샷 미실현손익
- `total_pnl`: realized + unrealized
- `win_rate`: 이긴 매도 / 전체 매도(closed)
- `average_win` / `average_loss`: 이긴/진 매도의 평균 실현손익
- `num_trades`: 전체 매매로그 건수 / `num_closed_trades`: 매도 건수
- `exposure_over_time`: 일별 노출(스냅샷 total_exposure)

## 함수
- `compute_performance(snapshots, trade_log, *, starting_cash) -> PerformanceReport` (순수).
- `performance_from_multiday(result) -> PerformanceReport`: MultiDayResult에서 snapshots(None 제외)·
  trade_log·starting_cash 추출해 compute_performance 호출.
- `format_performance_report(report) -> str`: 사람이 읽는 텍스트(측정 보조, 판단 아님).

## 엣지케이스 (fail-safe)
- 빈 snapshots/거래 없음 → equity_curve (), cumulative_return 0, max_drawdown 0, 모든 PnL 0,
  win_rate 0, 평균 0, num_trades 0. 크래시 없음.
- starting_cash ≤ 0 → cumulative_return 0(0분모 회피).
- 실현손익 0 매도는 win도 loss도 아님(breakeven).

## 비범위
- Sharpe/Sortino/벤치마크 비교(헌장 §9는 백테스트 엔진 도메인), 라이브 성과, 실주문, 전략/시그널 변경.
