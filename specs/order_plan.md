# SPEC: order_plan (사전 주문계획 진단 — 리포트 전용)

각 시뮬 진입/후보에 대해 **실행 전 명확한 주문계획**을 만든다. 청산 설정을 진입 전에 확정하고, 한정매수
(limit-buy) 로직과 결정론적 하드 청산을 기술한다. **측정 전용** — 스캐너/디시전/사이징/RiskGate/실제
시뮬 체결을 바꾸지 않는다. 어떤 새 규칙도 실 트레이드에 적용하지 않는다(can_trade_live=False).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 주문계획 원칙
- 진입은 blind market buy가 아니라 **limit_buy_shadow**(한정매수 그림자)로 표현 — 최대 슬리피지 상한.
- 청산 설정은 **진입 전에** 확정해 plan에 첨부(AI/API 지연이 진입 후 청산을 좌우하지 못함).
- 모멘텀 매매에 **고정 전량 익절(full take-profit) 기본 없음**. 하드 결정론 청산만:
  stop-loss, trailing-stop, time-cut, max-holding-days. partial take-profit은 **기술만**(미강제).

## plan 행 (OrderPlan)
- symbol, entry_date, reference_price.
- entry_order_type = "limit_buy_shadow".
- max_entry_slippage_pct, suggested_limit_price(= reference×(1+slippage); ref 결측/≤0이면 None — 안전).
- order_timeout_policy(= "cancel_end_of_day": 미체결 시 EOD 취소, 추격 금지).
- attached_exit_profile(ExitProfile): stop_loss_pct, trailing_stop_pct, max_holding_days,
  time_cut_days(optional), partial_take_profit(optional, 미강제).
- route_type: normal / leveraged_shadow / no_trade.
- can_trade_live = False (property), real_orders_placed = 0 (property).

## 청산 프로파일(현재 로버스트 기본)
- normal: stop_loss 0.15, trailing 0.20, max_holding 60.
- leveraged_shadow(그림자 전용): 더 타이트한 placeholder — stop 0.07, trailing 0.10, max_holding 10, time_cut 3.
- no_trade: 청산 프로파일 없음, suggested_limit_price None.

## 함수
- `build_order_plan(symbol, entry_date, reference_price, *, route="normal", max_slippage_pct=0.005, profile=None) -> OrderPlan`.
- `compute_order_plan_diagnostics(trade_diag, *, profile=None, max_slippage_pct=0.005) -> OrderPlanReport`.
- `format_order_plan(report) -> str`.

## run_sim 통합
- 베이스라인 섹션 뒤에 주문계획 섹션 출력/저장. 측정 전용(실제 진입 leg마다 normal 계획).

## 테스트 (tests/test_order_plan.py)
- 유효 시뮬 트레이드에 주문계획 렌더.
- exit 프로파일이 진입 전 첨부(plan에 stop/trail/max-holding 포함).
- limit price 계산 안전(ref 결측/≤0 → None, 예외 없음).
- 고정 전량 익절 미강제(partial_take_profit None 기본).
- leveraged_shadow 타이트 프로파일, no_trade 라우트.
- 진단이 trades/fills/vetoes 불변. can_trade_live False, real_orders_placed == 0.

## 비범위
- 실제 체결/슬리피지 모델 변경, partial TP 강제, 레버리지 실매매, 라이브 라우팅, 전략/시그널 변경.
