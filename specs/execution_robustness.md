# SPEC: execution_robustness (실행 정책 로버스트니스 검증 — 실험/리포트 전용)

next-open이 3% limit 대비 **시간창·심볼 의존·슬리피지**에 걸쳐 강건한지 검증한다. next-open을 기본 후보로
삼기 전에 한 심볼/한 국면 의존인지 본다. 기존 run_sim 로직으로 변형을 돌릴 뿐 새 매매 경로 없음.

베이스라인 잠금: max_holding 60, stop 0.15, trailing 0.20, fractional. winner extension 미적용, **갭 가드
미적용**. 레버리지 주말청산 opt-in 유지(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 실험 러너 — 측정만.

## 검증
1. 시간창 분할(분기): 정책별 robustness_report 윈도우(return/MDD/pnl/trades)를 분기별로 비교.
2. Leave-one-symbol-out: 20-심볼에서 심볼을 하나씩 빼고 next-open 재시뮬 → 각 심볼 의존도(full 대비 delta).
3. 슬리피지 강건성: 0/0.25/0.50/1.00%에서 next-open이 3% limit을 계속 이기는지(adj_pnl = pnl−entry×slip×qty).
4. 집중 경고: 한 심볼이 양수 PnL의 35% 초과 / 한 윈도우가 수익 대부분 / next-open이 한 좁은 구간에서만 우위.

## 출력 — RobustnessValidation
- full_limit3 / full_next_open(PolicySummary: return/MDD/win/pnl/trades).
- windows(WindowCompare: 분기별 limit3 vs next-open + next_open_wins).
- leave_one_out(LeaveOneOut: dropped_symbol/total_pnl/delta_vs_full/pct_of_full), worst_drop.
- slippage(SlippageCompare: slip/limit3_return/next_open_return/next_open_wins).
- best_window/worst_window(next-open return 기준), next_open_window_wins, warnings, is_robust, real_orders==0.

## 함수
- `compute_window_comparison(limit3_windows, next_open_windows) -> tuple`.
- `compute_leave_one_out(full_next_open_pnl, loo_pnl_by_symbol) -> tuple`.
- `compute_slippage_robustness(limit3_diag, next_open_diag, *, slippages, starting_cash) -> tuple`.
- `build_validation(limit3_summary, next_open_summary, windows, loo, slippage, next_open_symbol_pnl) -> RobustnessValidation`.
- `format_robustness_validation(report) -> str`.
- 러너 `scripts/execution_robustness.py`: 두 시뮬 + LOO 재시뮬을 60일 베이스라인 고정으로 돌려 검증.

## 테스트 (tests/test_execution_robustness.py)
- 윈도우 비교/LOO/슬리피지 그리드 동작. 집중 경고. is_robust 판정.
- 러너: 갭 가드 미적용, 베이스라인 60 고정, 두 실행만, real_orders_placed == 0.

## 비범위
- 실 혼합 실행 sim, 정확한 자본 재배분, 갭 가드 적용, 라이브 적용, 전략/시그널 변경, 베이스라인 변경.
