# SPEC: shadow_health (섀도 런 헬스 체크 / 데이터 신선도 가드 — 실험/리포트 전용)

일간 섀도 리포트가 stale·결측·중복·불완전 데이터를 조용히 쓰지 못하도록 입력/원장을 검증한다. 순수
검증 + 러너. 데이터/원장을 읽기만 하며 스캐너/디시전/RiskGate/베이스라인을 바꾸지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 진입/청산/유니버스/베이스라인 미변경.

## 점검
- 심볼별 최신 OHLCV 날짜, 요청 report date가 로컬 데이터에 있는지(거래일).
- 결측 심볼, stale 심볼(가장 신선한 날짜 대비 lag), 비거래일 날짜 처리, 부분 유니버스 커버리지.
- 결정 원장 중복 record_id, 결과 원장 중복, malformed JSONL 행, 필수 필드 누락.
- real_orders_placed는 항상 0(아니면 FAIL).

## 헬스 상태
- PASS / WARN / FAIL (전체 = 최악 finding).
- FAIL: 필수 원장 행 malformed, 또는 real_orders_placed != 0.
- WARN: 데이터 stale, 일부 심볼 결측, report date가 비거래일, 부분 커버리지, 중복 행.
- PASS: 데이터·원장이 사용 가능할 때만.

## 출력
- reports/shadow_health_check.md (사람용), reports/shadow_health_check.json (기계용).

## 일간 리포트 통합 (간단)
- daily_shadow_report 상단에 헬스 상태 + stale 경고 표시. FAIL이 아니면 리포트 생성을 막지 않는다.
  FAIL이면 생성 차단(데이터 사용 불가).

## 함수
- `worst_status(statuses) -> str`, `HealthFinding`, `HealthReport`.
- `build_health(*, universe, available_symbols, last_dates, report_date, trading_days, as_of,
  decision_records, decision_malformed, outcome_records, outcome_malformed, stale_days) -> HealthReport`.
- `format_health_markdown(report) -> str`, `health_to_json(report) -> dict`.
- 러너 `experiments/shadow_health_check.py` (`python -m experiments.shadow_health_check [--date]`).

## 테스트 (tests/test_shadow_health.py)
- 중복/ malformed / stale / 결측 심볼 / 비거래일 date / real_orders!=0 탐지.
- 베이스라인/유니버스/run_sim 기본값 불변, 브로커/라이브 미사용, report-only, real_orders==0.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스, 데이터 자동 갱신, 라이브.
