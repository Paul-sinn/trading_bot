# SPEC: next_bar_fill_whatif (다음 바 한정매수 체결 What-if — 리포트 전용)

같은-바 체결 what-if의 lookahead 우려를 해소한다. 시그널/참조는 entry_date지만, 한정매수는 **다음 거래
바**에 제출돼 체결됐을지 평가한다(참조 종가는 그 바가 끝나기 전엔 몰랐으므로 같은-바 체결은 과대평가될 수
있음). **측정/what-if 전용** — 실제 시뮬 체결/포트폴리오/매매/veto를 바꾸지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `order_plan_report`: OrderPlanReport. `price_data`: {symbol: OHLC}. `trade_diag`(선택): 실 PnL 조인.

## 다음-바 체결 모델
- 시그널/참조일 = entry_date. 주문은 **다음 거래 바**에 제출.
- `next_open <= limit_price` → next_open 체결.
- 아니고 `next_low <= limit_price <= next_high` → limit 체결.
- 그 외 → missed. 다음 바 결측(마지막 바였음/날짜 못 찾음) → unknown.
- cancel_end_of_day 존중 → 다음 바 단일만 평가(그 다음날 추격 없음).

## 리포트 (NextBarFillReport)
- same_bar_fill_rate vs next_bar_fill_rate(같은-바는 기존 모델로 함께 계산해 대조).
- next: filled/missed/unknown count, fill_rate.
- missed_profitable_count/pnl(다음-바 모델에서 놓친 수익 트레이드), avg_next_bar_gap(= next_open/ref−1).
- missed_by_symbol, best/worst missed(실 PnL 기준).
- warnings: 같은-바 체결률이 오해 소지로 높음(같은-바 ≥0.98 & 다음-바가 10%p+ 낮음),
  다음-바 모델이 진입 가능성을 크게 바꿈(|same−next| ≥ 0.10).
- real_orders_placed == 0 (property).

## 함수
- `compute_next_bar_fill_whatif(order_plan_report, price_data, *, trade_diag=None) -> NextBarFillReport`.
- `format_next_bar_fill_whatif(report) -> str`.

## run_sim 통합
- 같은-바 체결 what-if 섹션 뒤에 다음-바 섹션 출력/저장. 측정 전용.

## 테스트 (tests/test_next_bar_fill_whatif.py)
- 다음-바 open 체결 / 장중 limit 체결 / missed.
- 다음 바 결측 → unknown.
- 같은-바와 다음-바 결과가 다를 수 있음(갭업 시 같은-바 체결·다음-바 미체결).
- 입력(주문계획/price_data) 불변. real_orders_placed == 0.

## 비범위
- 분/틱 체결, 부분체결, 다음날 추격, 슬리피지 정밀모델, 실 라우팅/체결 변경, 전략/시그널 변경.
