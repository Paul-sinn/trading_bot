# SPEC: historical_sim (백테스트 데이터 → 멀티데이 시뮬 구동)

과거 일봉 데이터로 멀티데이 dry-run 루프를 구동한다. 일별로 point-in-time 슬라이스(미래참조 없음)를
scanner→evidence→RiskGate→시뮬주문→시뮬체결→포트폴리오→mark-to-market→청산→성과로 흘린다.

관련: `agents/data_adapter.py`(OHLCV DataFrame 포맷), `agents/scanner.py`(ScannerAgent,
MockPriceDataProvider), `agents/evidence.py`(build_contexts), `agents/multiday.py`(DayInput,
run_phase1_multiday), `agents/perf_report.py`(performance_from_multiday).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 튜닝 없음 — 기존 구조 재사용만.

CRITICAL (미래참조 금지): day D에는 D까지의 데이터만 본다 — 각 심볼/SPY/VIX/benchmark를 `df.loc[:D]`로
슬라이스. 상장 전·이력 부족 심볼은 후보가 안 됨(자연 제외).

CRITICAL (fail-closed): 데이터 결측(심볼 없음/이력 부족/그날 가격 없음)은 후보 미생성·data_missing 마킹·
사이징 무효로 막힌다(거래 안 됨).

## 함수
### `async run_historical_simulation(*, price_data, spy_prices, vix, policy, account_cash, benchmark_prices=None, trading_days=None, params=None, event_provider=None, warmup=200, default_exit_params=None) -> HistoricalResult`
1. `trading_days` 없으면 `spy_prices.index[warmup:]`(이력 충분 후 거래일).
2. 각 거래일 D: 심볼별 `df.loc[:D]` 슬라이스로 MockPriceDataProvider + ScannerAgent 구성,
   `await build_contexts(...)`로 컨텍스트(spy/vix/benchmark도 `.loc[:D]`), `mark_prices`=D 종가.
   `default_exit_params` 있으면 전 심볼에 청산 파라미터 부여(미보유는 apply_exit가 무시).
3. `await run_phase1_multiday(days, policy, account_cash)` → `performance_from_multiday`.

### `HistoricalResult` (frozen)
- `multiday: MultiDayResult`, `performance: PerformanceReport`
- `@property real_orders_placed -> 0`, `@property portfolio`, `@property trade_log`

## 엣지케이스
- 데이터 없는 심볼/이력 부족 → 그날 후보 없음(거래 안 됨).
- event_provider 없음 → 전 후보 veto(거래 없음). 그날 종가 결측 → 스냅샷 data_missing.
- 빈 trading_days → 빈 결과 + 초기 포트폴리오.

## 비범위
- 실브로커/LLM/이벤트 캘린더, Norgate 실로드(price_data 주입형), 러닝 equity 기반 동적 사이징(파라미터
  account_equity 고정 — 어포더빌리티는 러닝 현금으로 체크), Sharpe/벤치마크(헌장 §9 백테스트 엔진 도메인).
