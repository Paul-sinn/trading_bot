# SPEC: robustness_report (강건성/안정성 리포트 — 리포트 전용)

현재 시뮬 성과가 심볼·기간에 걸쳐 강건한지(한 심볼/한 구간에 의존하지 않는지) 점검한다. **측정 전용** —
스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고, 섀도 점수 필터를 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `multiday`: historical_sim 결과(트레이드 leg + equity 곡선 원천). price_data로 미청산 마크 계산.
- `price_data`: {symbol: OHLCV} — 미청산 포지션 마지막 종가(미실현 포함 손익).
- `trade_diag`(선택, 테스트용): TradeDiagnostics 직접 주입(주면 multiday/price_data 재계산 생략).
- `rerun_results`(선택): {제외심볼: HistoricalResult} — 실제 LOO 재시뮬이 가능할 때. 없으면 트레이드 제거 근사.

## 분석
- 기간 윈도우(분기): equity 곡선을 분기별로 묶어 start/end equity, return, pnl, 윈도우 내 MDD, 트레이드 수.
- best/worst 윈도우(return 기준).
- 심볼별 성과: trades, total_pnl, win_rate (실현+미실현).
- 집중 위험: 양수 손익 1위 심볼 비중(top_symbol_pnl_share).
- leave-one-symbol-out: rerun_results 있으면 실제 재시뮬, 없으면 해당 심볼 손익 제거 근사(total_pnl_diff).

## 경고
- 한 심볼이 **양수 손익의 50% 초과** 차지 → 집중 경고.
- top contributor 제거 시 총손익이 50% 이상 붕괴 → 의존 경고.
- 표본/기간 부족(trades < 4 또는 윈도우 < 2) → 신뢰도 경고.

## 출력 — RobustnessReport
- `windows`(WindowStat 튜플), `best_window`, `worst_window`, `symbol_perf`(SymbolPerf 튜플),
  `top_symbol`, `top_symbol_pnl_share`, `leave_one_out`(LeaveOneOut 튜플), `actual_total_pnl`,
  `warnings`, `real_orders_placed == 0`(property).

## run_sim 통합
- What-if 섹션 뒤에 강건성 섹션 출력/저장. 측정 전용(기본은 트레이드 제거 근사 — 추가 재시뮬 없음).

## 함수
- `compute_robustness_report(multiday, price_data, *, trade_diag=None, rerun_results=None) -> RobustnessReport`.
- `format_robustness_report(report) -> str`.

## 테스트 (tests/test_robustness_report.py)
- 분기 윈도우 통계(return/MDD/pnl) 정확.
- 심볼별 성과 + 집중 비중.
- 한 심볼 의존 경고 / top 제거 붕괴 경고.
- LOO 트레이드 제거 근사 + rerun_results 경로.
- 작은 표본 안전, 트레이드 없음 안전.
- 리포트 전용: 입력(trade_diag/multiday) 불변, 매매/veto 안 바뀜.
- real_orders_placed == 0.

## 비범위
- 실 LOO 재시뮬 강제(선택), 전략/시그널 변경, 파라미터 최적화, 라이브 데이터, 정확한 포지션 레벨 위험모형.
