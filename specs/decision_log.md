# SPEC: decision_log (시그널 결정 로그 / 섀도 트레이딩 기반 — 실험/리포트 전용)

전략이 매일 무엇을 결정하는지(BUY/REJECT/SKIP) 사람이 읽고 기계가 읽는 형식으로 기록한다. 향후 전진
검증(forward validation)을 위해 결정을 남기고 나중에 결과를 평가하기 위한 기반. **기존 스캐너/디시전/
RiskGate/베이스라인을 전혀 바꾸지 않고**, 기존 dry-run 산출물(Phase1Result의 DryRunReport.decisions)을
읽어 로그로 변환만 한다.

선택 날짜(또는 로컬 데이터의 최신 거래일)의 결정을 기록한다. run_sim.simulate를 end_date까지 돌려 마지막
day_result의 결정 행을 읽는다(지표는 point-in-time, 포지션 상태는 누적 시뮬 반영).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate·진입/청산/유니버스 미변경.

## 결정 분류
- **BUY**: effective_decision이 BUY(스캔 후보 + 판단 BUY + RiskGate 통과).
- **REJECT**: 스캔 후보지만 BUY 아님(RiskGate veto 또는 판단 비-BUY). 사유 기록.
- **SKIP**: 유니버스에 있으나 그날 스캐너 후보가 아님(필터 미통과).

## 레코드 필드 (심볼별)
- date, symbol, decision(BUY/REJECT/SKIP), reason 요약.
- 피처 스냅샷(가능하면): momentum_score, volume_ratio_20d, price_above_20ma, ma20_above_ma50,
  relative_strength, distance_from_high.
- shadow_score(가능하면, feature_shadow_score._score 재사용).
- riskgate: passed / veto reasons(가능하면).
- position_state: 보유 수량(가능하면).
- planned_entry_type = next-bar-limit, entry_limit_buffer_pct = 0.03.
- planned_exit: stop 0.15, trailing 0.20, max_holding 60.
- real_orders_placed = 0.

## 출력
- reports/signal_decision_log.md (사람용).
- reports/signal_decision_log.jsonl (기계용, append-friendly — 전진 검증 누적).

## 마크다운이 답할 것
- 오늘 어떤 심볼을 살까(BUY)?
- 어떤 심볼이 거절됐나(REJECT)?
- 왜 거절됐나(사유)?
- RiskGate가 무언가 veto했나?
- 라이브였다면 주문 계획은(planned entry/exit, report-only)?
- 주문이 없었음을 확인(real_orders_placed = 0).

## 함수
- `make_record(date, symbol, decision, *, reason, snapshot, shadow_score, riskgate_passed, riskgate_reasons, position_shares) -> DecisionRecord`.
- `build_decision_log(date, records) -> DecisionLog` (카운트 + real_orders==0).
- `records_to_jsonl(records) -> str`, `format_decision_log_markdown(log) -> str`.
- 러너 `experiments/signal_decision_log.py` (`python -m experiments.signal_decision_log [--date YYYY-MM-DD]`).

## 테스트 (tests/test_decision_log.py)
- 베이스라인/진입/청산 plan 상수 불변, 스캐너/디시전/사이징/RiskGate·run_sim 기본값 불변.
- 레코드가 필수 필드 포함, BUY/REJECT/SKIP 일관 표현, jsonl 라인이 유효 JSON.
- 마크다운이 6개 질문 섹션 포함, order plan은 report-only, real_orders_placed == 0.
- 러너가 출력 파일 생성, 브로커/라이브 미사용.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스 API, 미래 데이터 사용, 결과 평가(후속 단계).
