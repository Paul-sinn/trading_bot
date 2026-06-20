# SPEC: experiment_matrix (실험 매트릭스 러너 — 리포트/실험 전용)

여러 유니버스/설정 실험을 **기존 run_sim/historical_sim 로직 그대로** 돌려 한 표로 비교한다. 전략/스캐너/
디시전/사이징/RiskGate를 바꾸지 않고, 섀도 필터를 실 트레이드에 적용하지 않는다. 새 매매 경로 없음 —
run_sim.simulate를 호출만 한다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 실험 러너 — 측정/비교만.

## 실험 설정 (ExperimentConfig)
- name, data_root, benchmark(SPY 기본), symbols(없으면 폴더 전체), warmup(125),
  starting_cash(1000), share_mode(fractional), stop_loss_pct(0.15), trailing_stop_pct(0.20),
  max_holding_days(60), events_csv(data/events.csv 기본), assume_no_events(False), lot_size(0.001).
- 표준 유니버스: small=SPY,NVDA,AAPL,MSFT,AMD,GOOGL / expanded=20종목 텍 유니버스. 벤치마크 SPY 또는 QQQ.

## 한 실험 (ExperimentResult)
- name, symbols_count, trades, cumulative_return, max_drawdown, win_rate, total_pnl,
  top_symbol, top_symbol_pnl_share, robustness_warnings, error(실패 사유|None), real_orders_placed==0.
- 메트릭은 run_sim.simulate 결과(perf_report) + compute_robustness_report에서 읽는다.

## fail-closed (per-experiment)
- 데이터 폴더 없음/벤치마크 없음/events.csv 없음(바이패스 아님)/잘못된 설정 → run_sim.simulate가
  DataAdapterError. 매트릭스는 이를 해당 실험의 error로 담고 **나머지 실험은 계속**(전체 크래시 금지).
  가짜 메트릭을 만들지 않는다.

## 출력 — MatrixReport
- `results`(ExperimentResult 튜플), `real_orders_placed==0`(property).
- `format_matrix`: 실험별 한 줄 비교표(symbols/trades/cum_ret/MDD/win/total_pnl/top share/warn/orders).

## 함수
- `run_experiment(config, *, simulate_fn=None) -> ExperimentResult`(simulate_fn 주입 가능 — 테스트).
- `run_matrix(configs, *, simulate_fn=None) -> MatrixReport`.
- `format_matrix(report) -> str`.
- CLI: `--small-root`, `--expanded-root`, `--benchmark`, `--events-csv`, `--assume-no-events`, `--output`.

## 테스트 (tests/test_experiment_matrix.py)
- 두 유니버스 비교(주입 simulate_fn) — 결과 2건, 메트릭/경고 반영.
- 실 fixture 폴더 통합(_default_simulate) — 매매/veto 불변, real_orders 0.
- 누락 입력 fail-safe(error 담고 매트릭스 계속).
- format이 핵심 메트릭 포함.
- real_orders_placed == 0.

## 비범위
- 전략/시그널/파라미터 최적화, 새 매매 경로, 라이브 데이터/뉴스/이벤트, 섀도 필터의 실 적용.
