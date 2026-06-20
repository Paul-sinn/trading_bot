# SPEC: exit_sensitivity (청산 정책 민감도 매트릭스 — 실험/리포트 전용)

성과가 특정 청산 설정(stop 15% / trailing 20% / max-holding 60d)에 과도하게 의존하는지 그리드 스윕으로
점검한다. 기존 run_sim 로직 그대로(청산 플래그만 바꿔) 돌린다. 전략/스캐너/디시전/사이징/RiskGate를
바꾸지 않고, 어떤 새 규칙도 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

## 고정 그리드 (최적화 아님)
- stop_loss_pct: 0.10, 0.15, 0.20
- trailing_stop_pct: 0.15, 0.20, 0.25
- max_holding_days: 45, 60, 90
- 곱집합 27개 조합. 기본(default) = (0.15, 0.20, 60).

## 한 조합 (ExitRunResult)
- stop_loss_pct, trailing_stop_pct, max_holding_days,
- cumulative_return, max_drawdown, win_rate, total_pnl, trades,
- return_mdd_ratio(= cum/MDD, MDD>0일 때만), robustness_warnings, error(|None), real_orders_placed==0.
- 메트릭은 run_sim.simulate 결과 + compute_robustness_report에서 읽는다.

## fail-closed
- 데이터 폴더/벤치마크/events 누락은 run_sim이 DataAdapterError → 해당 조합 error로 담는다(가짜 메트릭
  금지). 전부 실패면 bests=None + 경고(전체 크래시 금지).

## 출력 — ExitSensitivityReport
- `results`(27), `best_by_return`, `best_by_return_mdd`, `safest_by_mdd`(최저 MDD), `default_result`,
  `warnings`, `real_orders_placed==0`(property).

## 경고
- 단일 설정 의존: 최고수익의 80% 이상인 조합이 1개뿐이면 경고(과적합 위험).
- 파라미터 민감/붕괴: 성공 조합 수익의 상대 스프레드((max−min)/max) > 0.5이거나 일부 조합이 음수 수익이면 경고.

## 함수
- `generate_grid(stop_grid, trail_grid, hold_grid) -> list[tuple]`.
- `run_one(config, stop, trail, hold, *, simulate_fn=None) -> ExitRunResult`.
- `run_sensitivity(config, *, simulate_fn=None) -> ExitSensitivityReport`.
- `format_exit_sensitivity(report) -> str`.
- CLI: `--data-root`, `--benchmark`, `--symbols`, `--warmup`, `--events-csv`, `--assume-no-events`, `--output`.

## 테스트 (tests/test_exit_sensitivity.py)
- 그리드 생성(27개, default 포함).
- 결과 집계(best by return / return-MDD / safest by MDD).
- return/MDD 비율.
- 단일 설정/민감 붕괴 경고.
- 데이터 누락 fail-closed(error 담고 크래시 없음).
- 입력/매매 불변, real_orders_placed == 0.

## 비범위
- 청산 파라미터 최적화/학습, 새 규칙의 실 매매 적용, 전략/시그널 변경, 라이브 데이터.
