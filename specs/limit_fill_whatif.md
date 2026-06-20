# SPEC: limit_fill_whatif (한정매수 체결 What-if — 리포트 전용)

사전 주문계획(order_plan)의 limit_buy_shadow가 일봉 OHLC로 **체결됐을지** 추정한다. **측정/what-if 전용** —
실제 시뮬 체결/포트폴리오/매매/veto를 바꾸지 않는다. 어떤 결과도 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `order_plan_report`: OrderPlanReport(limit_buy_shadow 계획들).
- `price_data`: {symbol: OHLC DataFrame}(진입일 일봉).
- `trade_diag`(선택): 실 트레이드 PnL 조인용(best/worst missed 계산).

## 일봉 체결 모델(매수 한정가)
limit_price 기준 진입일 OHLC로:
- `open <= limit_price` → 체결 at open(shadow_fill_price=open).
- 아니고 `low <= limit_price <= high` → 체결 at limit(shadow_fill_price=limit_price).
- 그 외 → missed(미체결).
- 진입일 OHLC 결측 → unknown(가짜 체결 금지).
- order_timeout_policy = cancel_end_of_day 존중 → 진입일 단일 바만 평가(다음날 추격 없음).

## 리포트 (LimitFillReport)
- total_planned, filled_count, missed_count, unknown_count, fill_rate(= filled / 알려진(filled+missed)).
- avg_limit_distance(= mean((limit−ref)/ref)).
- missed_by_symbol(미체결 최다 심볼), best_missed/worst_missed(실 PnL 기준 미체결 최고/최악).
- warnings: 수익 트레이드 상당부분이 미체결로 누락(수익의 20%+), 상한이 빡빡(체결률<0.8)/느슨(체결률≥0.98).
- real_orders_placed == 0 (property).

## 함수
- `compute_limit_fill_whatif(order_plan_report, price_data, *, trade_diag=None) -> LimitFillReport`.
- `format_limit_fill_whatif(report) -> str`.

## run_sim 통합
- 주문계획 섹션 뒤에 체결 what-if 섹션 출력/저장. 측정 전용(feat_price_data OHLC 사용).

## 테스트 (tests/test_limit_fill_whatif.py)
- open ≤ limit → open 체결.
- 장중 low가 limit 터치 → limit 체결.
- 한 번도 limit 도달 못함 → missed.
- OHLC 결측 → unknown(가짜 체결 없음).
- 수익 미체결 경고 / best·worst missed.
- 입력(주문계획/price_data/trade_diag) 불변. real_orders_placed == 0.

## 비범위
- 분/틱 체결, 부분체결/큐 우선순위, 슬리피지 정밀모델, 다음날 추격, 실 라우팅, 전략/시그널 변경.
