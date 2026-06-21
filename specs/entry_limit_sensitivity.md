# SPEC: entry_limit_sensitivity (진입 한정가 민감도 매트릭스 — 리포트 전용)

여러 limit 버퍼의 **다음-바 체결 결과**를 비교해 현실적인 진입 한정가 정책을 찾는다. **측정/what-if 전용** —
실제 시뮬 체결/포트폴리오/매매/veto를 바꾸지 않는다. 임계값 최적화 없음(고정 그리드).

문제: 같은-바 체결은 lookahead였고, 다음-바 모델에서 0.5% 상한은 너무 빡빡(체결률 ~77%, 수익 트레이드
누락). 고정·비최적화 민감도 점검이 필요하다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `trade_diag`: TradeDiagnostics(진입 symbol/entry_date/entry_price=reference/pnl).
- `price_data`: {symbol: OHLC}(다음 바 평가용).

## 고정 정책(최적화 아님)
- limit 버퍼: 0.5%, 1.0%, 1.5%, 2.0%, 3.0% — limit = reference×(1+buffer), 다음-바 체결 평가.
- next-open marketable proxy: 다음 바 존재 시 next_open에 체결(상한 무시) — **worst-price-control /
  high-fill 모드로 명확 표기**. 다음 바 결측은 unknown.
- 다음-바 체결 모델: next_open≤limit→open, 아니면 next_low≤limit≤next_high→limit, 그 외 missed.

## 정책별 리포트 (PolicyResult)
- fill_rate, filled/missed/unknown, profitable_missed_count, missed_profitable_pnl,
  avg_next_bar_gap, avg_fill_premium(= fill/ref−1, 체결분), est_filled_pnl(체결분 실 PnL 합 —
  **what-if proxy, 전체 포트폴리오 시뮬 아님**), warnings(빡빡/느슨).

## 출력 — EntryLimitSensitivityReport
- `policies`(튜플), `best_by_fill_rate`, `best_by_est_pnl`, `recommended`(체결률 ≥0.95 최소 버퍼),
  `warnings`, `real_orders_placed == 0`(property).

## 함수
- `generate_buffers() -> tuple[float,...]`.
- `compute_entry_limit_sensitivity(trade_diag, price_data) -> EntryLimitSensitivityReport`.
- `format_entry_limit_sensitivity(report) -> str`.

## run_sim 통합
- 다음-바 체결 what-if 섹션 뒤에 진입 한정가 민감도 섹션 출력/저장. 측정 전용.

## 테스트 (tests/test_entry_limit_sensitivity.py)
- 버퍼 그리드 생성.
- 버퍼가 넓어지면 체결률 비감소.
- marketable proxy는 다음 바 있으면 체결.
- 다음 바 결측은 unknown 유지.
- 입력(trade_diag/price_data) 불변. real_orders_placed == 0.

## 비범위
- 버퍼 최적화/학습, 실제 체결/포트폴리오 변경, 정확한 자본 재배분 시뮬, 분/틱 체결, 전략/시그널 변경.
