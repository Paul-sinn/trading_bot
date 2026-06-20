# SPEC: trade_diagnostics (시뮬 매매 레벨 진단 리포트)

multiday/historical 시뮬 결과에서 **매매 단위 진단**을 산출하는 순수 측정 모듈. 상태를 바꾸지 않고
기존 산출물(trade_log, daily_snapshots, day report decisions)만 읽는다. run_sim이 성과 리포트에
이어 출력한다.

관련: `agents/sim_portfolio.py`(TradeRecord, PortfolioSnapshot), `agents/multiday.py`(MultiDayResult),
`agents/historical_sim.py`(HistoricalResult), `agents/dry_run.py`(DryRunDecision veto/rationale),
`agents/perf_report.py`(성과 측정 — 별도).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/이벤트 캘린더 미연결. **리포트 전용 — 동작 변경 없음**(읽기만).

## 날짜 복원 (가정 금지, 동작 변경 없음)
TradeRecord에 날짜 필드가 없으므로 일별 `report_date` + 누적 `snapshot.trade_count`로 각 매매의
날짜를 복원한다(day i의 누적건수 구간 [prev,count)에 속한 매매 = day i). 스냅샷 결측일은 건너뛴다.

## 산출 (TradeDiagnostics)
- **trade list (TradeLeg)**: symbol, entry_date, exit_date, entry_price, exit_price, qty, pnl,
  pnl_pct, exit_reason. 미청산 포지션은 exit_reason="OPEN"; final_prices 주어지면 미실현 pnl,
  없으면 pnl/pnl_pct=None. 매수→매도 FIFO 매칭(분수주 지원).
- **best_trade / worst_trade**: pnl 있는 leg 중 최대/최소(없으면 None).
- **drawdown(DrawdownPeriod)**: peak_date/peak_equity, trough_date/trough_equity, max_drawdown,
  recovery_date(트로프 이후 peak_equity 재달성 첫 날, 없으면 None).
- **exposure_over_time / equity_over_time**: (date, 값) 시퀀스(스냅샷 있는 날만).
- **top_symbols_by_pnl**: 심볼별 pnl 합 내림차순.
- **top_veto_reasons**: 모든 day decisions의 veto 사유 빈도 내림차순.
- **entry_evidence(per trade if available)**: 진입일·심볼의 decision rationale 스냅샷
  (tier/weight/account_loss/rationale). 없으면 None.
- `real_orders_placed` property == 0.

## 함수
- `compute_trade_diagnostics(multiday, *, final_prices=None) -> TradeDiagnostics`.
- `format_trade_diagnostics(diag) -> str` (사람이 읽는 텍스트, 측정 보조 — 판단 아님).
- multiday는 `.day_results`(각 `.report.report_date/.portfolio_snapshot/.decisions`) + `.portfolio.trade_log`만
  요구(덕타이핑 — HistoricalResult.multiday 그대로).

## run_sim 통합
- 성과 리포트 뒤에 진단 리포트도 출력/저장(동작 변경 없음, 측정만). 미청산 포지션 미실현 pnl용
  final_prices는 마지막 거래일 종가에서 구한다.

## 테스트 (tests/test_trade_diagnostics.py) — fixture 데이터
- 청산 매매: pnl/pnl_pct/exit_reason 정확, best/worst.
- 미청산 매매: exit_reason="OPEN", final_prices로 미실현 pnl.
- drawdown 기간(peak/trough/recovery) 산출.
- exposure/equity 시퀀스, top_symbols_by_pnl, top_veto_reasons.
- 진입 증거 스냅샷 첨부.
- real_orders_placed == 0. (실 historical_sim 결과로도 1건 스모크)

## 비범위
- 라이브 체결/브로커, 전략·시그널 변경, 세금/수수료 모델, 벤치마크 상대성과.
