# SPEC: baseline_robustness (현실 베이스라인 전략 강건성 리포트 — 실험/리포트 전용)

잠긴 현실 베이스라인(next-bar-limit 3%, 60일)에서 **전략 전체**가 시간창·심볼·벤치마크·스트레스
가정에 강건한지 검증한다. 한 기간/한 심볼/한 국면에 의존하는지 본다. 새 매매 경로 없음 — 기존
run_sim 시뮬 산출물 + robustness_report/baseline_comparison 빌딩블록을 묶어 측정만 한다.

잠긴 베이스라인: entry_fill_model next-bar-limit, entry_limit_buffer_pct 0.03, max_holding 60,
stop 0.15, trailing 0.20, fractional. winner extension 미적용, **갭 가드 미적용**, next-open 미승격.
레버리지 주말청산 opt-in 유지(일반주 미적용, 기본 빈 집합).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 기본 동작 불변.

## 검증 (20-심볼 expanded)
1. 풀기간 베이스라인 결과: return/MDD/win/PnL/trades + return/MDD 비율.
2. 분기 윈도우: return/MDD/win/trades/PnL (robustness_report 윈도우 재사용).
3. Leave-one-symbol-out: 심볼을 하나씩 빼고 잠긴 베이스라인 재시뮬 → return/MDD/PnL delta. 한 심볼이
   수익을 과도하게 끌면 경고.
4. 벤치마크 비교: SPY / QQQ / equal-weight 20-심볼 바스켓(baseline_comparison 재사용, 결측은 안전 처리).
5. 슬리피지/비용 스트레스: 0/0.25/0.50/1.00% (adj_pnl = pnl − entry×slip×qty, 단일 정책).
6. 집중 점검: top 심볼 PnL share, top 3 심볼 PnL share, worst 심볼 제거 영향.
7. 청산 사유 분포: time_stop / trailing_stop / stop_loss / other(개수·PnL·비중).

## 출력 — BaselineRobustness
- full(BaselineFull: cumulative_return/max_drawdown/win_rate/total_pnl/trades/return_over_mdd).
- robustness(RobustnessReport 재사용: windows/symbol_perf/leave_one_out/top share/warnings).
- benchmark(BaselineComparison 재사용: SPY/QQQ/equal-weight/best-single + 결측 note).
- slippage(SlippageStress: slippage/total_pnl/return_pct, 단일 정책).
- concentration(Concentration: top_symbol/top1_share/top3_share/top3_symbols/worst_symbol/
  worst_removal_pnl/worst_removal_delta).
- exit_reasons(ExitReasonStat: reason/count/total_pnl/share).
- best_window/worst_window, warnings, is_robust, beats_spy, beats_qqq, survives_slippage, real_orders==0.

## 함수
- `compute_full_result(performance) -> BaselineFull`.
- `compute_slippage_stress(diag, *, slippages, starting_cash) -> tuple` (입력 불변, 원본 미변형).
- `compute_exit_reason_distribution(diag) -> tuple` (time_stop/trailing_stop/stop_loss/other 버킷).
- `compute_concentration(symbol_totals) -> Concentration` (top1/top3/worst 제거).
- `build_baseline_robustness(full, robustness, benchmark, slippage, exit_reasons, concentration) -> BaselineRobustness`.
- `format_baseline_robustness(report) -> str`.
- 러너 `scripts/baseline_robustness.py`: 잠긴 베이스라인 풀런 + LOO 재시뮬로 리포트 생성.

## 테스트 (tests/test_baseline_robustness.py)
- 잠긴 베이스라인 설정 사용(next-bar-limit/0.03/60/0.15/0.20/fractional), next-open 미사용, winner extension 미사용.
- 윈도우 분할 동작, LOO 동작, 벤치마크 결측 안전 처리, 슬리피지 스트레스가 원본 diag를 변형하지 않음.
- 청산 사유 분포/집중 계산. real_orders_placed == 0.

## 비범위
- 실 혼합 실행 sim, 자본 재배분 변경, 갭 가드/winner extension 적용, next-open 승격, 라이브, 전략/시그널/베이스라인 변경.
