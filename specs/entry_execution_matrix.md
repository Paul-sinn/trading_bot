# SPEC: entry_execution_matrix (진입 실행 매트릭스 — 실험/리포트 전용)

현실적 진입 실행 정책(current / next-bar-limit 1·2·3% / next-open)을 **실제 시뮬**로 비교한다. 60일
베이스라인을 그대로 두고 entry_fill_model/buffer만 바꿔 run_sim.simulate를 호출한다. 새 매매 경로 없음.

베이스라인 잠금: max_holding 60, stop 0.15, trailing 0.20, share_mode fractional. winner extension 미적용.
레버리지 주말청산은 opt-in·레버리지 전용 유지(이 매트릭스는 weekend_exit_symbols 비움 — 일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 실험 러너 — 측정만.

## 비교 정책(고정)
1. current
2. next-bar-limit buffer 1%
3. next-bar-limit buffer 2%
4. next-bar-limit buffer 3%
5. next-open
고정 청산: stop 0.15, trailing 0.20, max_holding 60.

## 정책별 리포트 (PolicyResult)
name, entry_fill_model, buffer, cumulative_return, max_drawdown, win_rate, total_pnl, trades,
avg_holding_days, return_mdd_ratio, top_symbol/top_symbol_pnl_share, exit_reason 분포,
weekend_exit_count(=0), error(|None), real_orders_placed == 0(property).

## 함수
- `generate_policies() -> tuple`.
- `run_policy(data_settings, name, model, buffer, *, simulate_fn=None) -> PolicyResult`.
- `compute_entry_execution_matrix(*, data_root, benchmark, symbols, events_csv, assume_no_events, simulate_fn=None) -> ExecutionMatrixReport`.
- `format_entry_execution_matrix(report) -> str`. CLI: --data-root/--benchmark/--symbols/--events-csv/--output.
- ExecutionMatrixReport: policies, best_by_return_mdd, best_by_return, real_orders_placed == 0.

## fail-closed
- 데이터/벤치마크/events 누락은 run_sim이 DataAdapterError → 해당 정책 error로 담고 나머지 계속.

## 테스트 (tests/test_entry_execution_matrix.py)
- entry_fill_model/buffer 지원(정책별 args 반영).
- 베이스라인 max_holding 60 / winner extension 미적용 / weekend_exit_symbols 비움(일반주 미적용).
- default current 동작 불변(current 정책은 entry_fill_model=current).
- 리포트 핵심 메트릭 포함. real_orders_placed == 0.

## 비범위
- winner extension 적용, 레버리지 실매매, 전략/시그널 변경, 베이스라인 변경, 분/틱 체결.
