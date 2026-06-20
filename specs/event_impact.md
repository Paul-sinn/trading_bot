# SPEC: event_impact (이벤트 캘린더 영향 진단)

events.csv가 historical_sim 결과를 `--assume-no-events` 대비 어떻게 바꾸는지 설명하는 **순수 측정**
모듈. 상태/매매를 바꾸지 않고 day report decisions(veto 사유) + 이벤트 provider만 읽는다.

관련: `agents/event_calendar.py`(EventCalendarProvider.events_on), `agents/dry_run.py`(DryRunDecision:
symbol/raw_decision/veto.reasons), `algorithms/policy.py`(이벤트 veto 사유 "고임팩트 이벤트 리스크 미확인").

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## blocked 판정 (가정 금지)
후보의 veto 사유에 이벤트 사유("이벤트 리스크 미확인" 포함)가 있으면 "이벤트로 차단됨". medium/low는
provider가 애초에 차단하지 않으므로(event_risk_checked=True) 이 사유가 없어 차단 목록에 안 나타난다.

## 단일 런 진단 (EventImpactReport)
- num_blocked: 이벤트로 차단된 (날짜×심볼) 후보 수.
- by_symbol / by_event_type / by_date: 차단 건수 집계(event_type/severity는 provider.events_on 조회).
- would_have_been_buy: raw_decision==BUY **이고** 이벤트가 유일한 veto 사유일 때 True(이벤트만 없었으면
  매수). would_be_buy_count = 합.
- top_event_veto_reasons: 이벤트 관련 veto 사유 빈도.
- symbols_affected: 차단된 심볼 집합.
- real_orders_placed == 0 (property).

## 두 런 비교 (RunComparison) — 선택
assume-no-events 결과 vs events-csv 결과:
- trade_count(bypass/events/diff), cumulative_return(diff), max_drawdown(diff).
- symbols_affected: 매수 심볼 집합의 대칭차(진입이 달라진 심볼).
- real_orders_placed == 0.

## 함수
- `compute_event_impact(multiday, *, event_provider=None) -> EventImpactReport`.
- `format_event_impact(report) -> str`.
- `compare_runs(bypass_result, events_result, *, event_provider=None) -> RunComparison`.
- `format_comparison(cmp) -> str`.

## run_sim 통합
- events-csv 사용 시 성과/매매 진단 뒤에 event_impact도 출력/저장.
- `--compare-assume-no-events`(선택, events-csv 필요): 같은 설정으로 bypass 런을 한 번 더 돌려 비교 출력.
  주 결과는 events-csv 런 그대로 — 비교는 측정용 추가 실행(동작 변경 없음). events-csv 없으면 fail-closed.

## 테스트 (tests/test_event_impact.py)
- high MARKET 이벤트가 blocked 진단에 나타남.
- ticker 이벤트는 해당 심볼만 blocked.
- medium/low 이벤트는 blocked로 안 나타남.
- 진단이 매매를 바꾸지 않음(trade_log 불변).
- would_have_been_buy: 이벤트가 유일 사유 + raw BUY일 때만 True.
- compare_runs diff/symbols_affected.
- real_orders_placed == 0.

## 비범위
- 라이브 이벤트 API, 전략/시그널 변경, 이벤트별 가격충격 모델, 전방 윈도우 튜닝.
