# SPEC: winner_extension_whatif (선택적 승자 연장 What-if — 리포트 전용)

수익이 난 60일 time_stop 청산을 **추세가 여전히 건강할 때만** 연장했다면 어땠을지 본다. **측정/what-if
전용** — 기본 전략/스캐너/디시전/사이징/RiskGate/실 trade_log/포트폴리오를 바꾸지 않는다. 손실·불건강
포지션은 연장하지 않는다.

베이스라인 잠금: max_holding 60, stop 0.15, trailing 0.20, entry next-bar-limit 0.03, fractional. 레버리지
주말청산 코드는 opt-in·레버리지 전용 유지(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용.

## 입력
- `trade_diag`: TradeDiagnostics(트레이드 leg: symbol/entry_date/exit_date/entry_price/qty/pnl/exit_reason).
- `price_data`: {symbol: OHLC}(추세 조건 + 미래 바). `benchmark_prices`(선택): 상대강도/레짐.

## 분류
- time_stop 청산만 대상. pnl>0 = 수익, pnl≤0 = 손실.
- 수익 time_stop의 청산일에 건강 조건 확인(미래 바 있어야 연장 가능):
  price>50MA, price>20MA, 상대강도>0(벤치 있으면), SPY가 50MA 위(risk-off 아님), 미래 가격 데이터 존재.
  (time_stop으로 나갔으므로 trailing은 미히트 — 구조상 충족.)

## What-if 연장(건강+수익만)
- 청산 이후부터 90일/120일(entry 기준) 또는 기존 stop(진입가×(1−0.15))/trailing(추적고점×(1−0.20)) 히트까지 보유.
- 손실·불건강은 연장 안 함. 실 trade_log/포트폴리오 불변.
- 미래 데이터 없으면 안전 reject(no_future_data).

## 리포트 (WinnerExtensionReport)
- num_time_stop_exits, profitable_count, losing_count, healthy_candidate_count, rejected_count.
- rejected_reasons(사유별 집계). baseline_pnl_candidates, whatif_pnl_90, whatif_pnl_120,
  incremental_90/120, added_drawdown proxy. top_benefit(연장 이득 심볼), top_giveback(반납 심볼).
- candidates: ExtensionCandidate(symbol/dates/baseline_pnl/healthy/reject_reasons/pnl_90/120/
  reason_90/120/added_dd_90/120/incremental_90/120). real_orders_placed == 0(property).

## 함수
- `compute_selective_winner_extension(trade_diag, price_data, *, benchmark_prices=None) -> WinnerExtensionReport`.
- `format_selective_winner_extension(report) -> str`.

## run_sim 통합
- 진입 한정가 민감도 섹션 뒤에 출력/저장. 측정 전용.

## 테스트 (tests/test_winner_extension_whatif.py)
- 손실 time_stop은 절대 연장 안 됨.
- 수익이지만 불건강(50MA 아래 등)은 연장 안 됨(reject 사유).
- 건강+수익은 90/120 what-if 행 생성.
- 미래 데이터 없음 안전 reject.
- 입력(trade_diag/price_data) 불변. real_orders_placed == 0.

## 비범위
- 실 sim 동적 연장(보유 중 조건부), 정확한 자본 재배분, 분/틱 체결, 전략/시그널 변경, 베이스라인 변경.
