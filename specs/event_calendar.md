# SPEC: 이벤트 캘린더 CSV provider (수동, 로컬)

`--assume-no-events`(개발 바이패스)를 **로컬 events.csv** 기반 이벤트 리스크 provider로 대체한다. 기존
`EventRiskProvider` 인터페이스(`is_clear`)를 구현해 historical_sim/run_sim에 그대로 꽂는다. 라이브 이벤트
API 아님 — CSV 파일 입력형.

관련: `agents/evidence.py`(EventRiskProvider Protocol, build_candidate_context의 event_ok),
`algorithms/policy.py`(event_risk_checked=False → hard-veto "고임팩트 이벤트 리스크 미확인").

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM 미연결. 라이브 이벤트
API 미연결. 전략 시그널 튜닝 없음. CSV provider만.

CRITICAL (fail-closed, 가정 금지): events.csv 필수 컬럼 누락/날짜 무효 → EventCalendarError. as_of 날짜를
모르면(None) is_clear=False(확인 불가 → 안전). run_sim에서 --events-csv도 --assume-no-events도 없으면
fail-closed(exit 2).

## CSV 포맷
`date,event_type,ticker,severity,notes` (대소문자 무시). 예:
```
2026-01-28,earnings,MSFT,high,quarterly earnings
2026-03-20,FOMC,MARKET,high,Fed decision
2026-04-10,CPI,MARKET,high,inflation report
```
- `ticker=MARKET`: 전 심볼에 적용(FOMC/CPI 등). 그 외 ticker: 해당 심볼만.
- `severity`: **high만 진입 차단**(권장 정책). medium/low는 차단 안 함(통과 — 플래그용). 차단 severity는
  설정 가능(`block_severities`, 기본 ("high",)).

## provider 동작 (EventCalendarProvider)
- `is_clear(symbol, as_of=None) -> bool`: as_of 날짜에 symbol 또는 MARKET 대상 **차단 severity** 이벤트가
  있으면 False(이벤트 리스크 → event_risk_checked=False → 기존 hard-veto가 진입 차단). 없으면 True(clear).
  as_of=None이면 False(fail-closed). 윈도우(`window_days`, 기본 0=당일)로 전방 매칭 가능.
- `from_csv(path)` / `from_frame(df)`: 로드 + 검증(fail-closed). `events_on(symbol, as_of)`: 진단/증거용.
- 이벤트 차단은 기존 RiskGate 불리언 게이트와 정확히 맞물림 — 전략/리스크 규칙 변경 없음.

## evidence 연동(날짜 인지)
- `EventRiskProvider` Protocol의 `is_clear`에 선택 `as_of` 추가(MockEventRiskProvider는 무시 — 하위호환).
- `build_candidate_context`는 point-in-time df의 마지막 날짜(df.index[-1])를 as_of로 넘긴다.
- 차단 시 veto 사유("고임팩트 이벤트 리스크 미확인")가 trade_diagnostics top_veto_reasons에 표시됨.

## run_sim CLI
- `--events-csv` (선택): events.csv 경로 → EventCalendarProvider.
- `--assume-no-events` (선택, **개발 바이패스 전용**): MockEventRiskProvider(default=True) 유지.
- 둘 다 없으면 fail-closed(DataAdapterError → exit 2). 둘 다 있으면 --events-csv 우선(명시 데이터).

## 테스트 (tests/test_event_calendar.py)
- 유효 events.csv 로드. 필수 컬럼 누락/날짜 무효 → fail-closed.
- MARKET 이벤트는 전 심볼 차단, ticker 이벤트는 해당 심볼만.
- high만 차단, medium/low 통과. as_of=None → False(fail-closed).
- run_sim: --events-csv 로드, 둘 다 없으면 fail-closed, --assume-no-events 바이패스 유지.
- real_orders_placed == 0.

## 비범위
- 라이브 이벤트 API, 자동 earnings 수집, 전략/시그널 변경, 멀티데이 전방 윈도우 튜닝.
