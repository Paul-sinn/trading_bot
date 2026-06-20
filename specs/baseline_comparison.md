# SPEC: baseline_comparison (벤치마크/베이스라인 비교 — 리포트 전용)

전략 성과를 단순 매수보유 베이스라인과 비교해 buy-and-hold를 넘어 가치를 더하는지 본다. **측정 전용** —
스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고, 섀도 필터를 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `performance`: 전략 성과(cumulative_return, max_drawdown, equity_curve).
- `price_data`: {symbol: OHLCV}(베이스라인용 — 벤치마크 SPY/QQQ 포함 전체).
- `universe`(선택): equal-weight/best-single 대상 트레이드 심볼(없으면 price_data에서 aux 제외).
- `start`/`end`(선택): 베이스라인 계산 구간(전략 거래창과 맞춤). 없으면 각 심볼 전체.

## 베이스라인
- `SPY buy-hold`: SPY 종가 매수보유.
- `QQQ buy-hold`(있으면): QQQ 매수보유. 없으면 None + note(안전).
- `equal-weight`: universe 동일가중 매수보유(정규화 종가 평균 곡선, 교집합 날짜 정렬).
- `best-single (hindsight)`: universe 중 사후 최고 수익 단일 종목 — **hindsight-only로 명확 표기**.

## 지표(베이스라인별)
- cumulative_return, max_drawdown, volatility(일수익 표준편차×√252, 쉬울 때).
- return_diff_vs_strategy = 전략수익 − 베이스라인수익. mdd_diff_vs_strategy = 전략MDD − 베이스라인MDD.

## 경고
- 전략이 단순 매수보유(SPY/QQQ/equal-weight, hindsight 제외)에 **미달**하면 해당 베이스라인 경고.
- 성과 대부분이 시장/섹터 강세로 설명될 수 있음: passive 베이스라인 최고수익이 전략수익의 70% 이상이면 경고.

## 출력 — BaselineComparison
- strategy_return, strategy_max_drawdown, strategy_volatility, `baselines`(BaselineResult 튜플),
  `warnings`, `real_orders_placed == 0`(property).

## run_sim 통합
- 강건성 섹션 뒤에 베이스라인 비교 섹션 출력/저장. 측정 전용. 데이터 로드 실패는 섹션만 비운다(fail-safe).

## 함수
- `compute_baseline_comparison(performance, price_data, *, universe=None, start=None, end=None,
  benchmark_symbol="SPY", qqq_symbol="QQQ") -> BaselineComparison`.
- `format_baseline_comparison(report) -> str`.

## 테스트 (tests/test_baseline_comparison.py)
- SPY 매수보유 수익/MDD 정확.
- equal-weight 베이스라인 정확.
- best-single hindsight 표기.
- QQQ/벤치마크 결측 안전.
- 전략−베이스라인 차이 + 미달/강세장 경고.
- 입력 불변(매매/veto 안 바뀜). real_orders_placed == 0.

## 비범위
- 팩터/리스크 모델, 알파 회귀, 거래비용 정밀화, 라이브 데이터, 베이스라인을 실 매매에 적용.
