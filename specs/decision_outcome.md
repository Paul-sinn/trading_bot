# SPEC: decision_outcome (결정 결과 채점 / 전진 검증 평가 — 실험/리포트 전용)

결정 로그(JSONL)의 각 결정을 미래 가격 결과로 채점한다. BUY가 실제로 올랐는지, REJECT가 옳았는지,
SKIP은 카운트만. 미래 데이터가 부족하면 unscorable로 표기(크래시 금지). **기존 스캐너/디시전/RiskGate/
베이스라인을 바꾸지 않고**, 로그/시뮬 산출물 + 로컬 OHLCV만 읽어 사후 측정한다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 진입/청산/유니버스/베이스라인 미변경.

## 입력
- reports/signal_decision_log.jsonl (기본). 로컬 OHLCV 디렉토리. --from-date/--to-date(선택).
- --backfill: 라이브 로그가 비면 historical sim의 day_results 결정(BUY/REJECT)을 재구성해 채점(실데이터 BUY/REJECT 확보).

## 전진 결과 (심볼/날짜별, ref = as_of 종가)
- 1/5/10/20/60 거래일 forward return.
- 60d 내 max favorable excursion(고가 기준), max adverse excursion(저가 기준).
- stop_loss 15% 발동 여부(저가가 ref×0.85 이하 도달).
- trailing_stop 20% 발동 여부(러닝 피크 대비 20% 하락).
- max_holding 60 청산 여부(stop/trail 미발동 + 60바 도달).
- 미래 바 부족 시 해당 지표 None, 1바도 없으면 unscorable(reason 기록).

## 결정별 채점
- BUY: avg/median forward return, 5/10/20/60d hit rate(>0), avg MFE/MAE, stop/trail/time-stop 시뮬 비율.
- REJECT: 나중에 잘 간(놓친 승자) / 손실 피한(옳은 거절) 심볼, 공통 거절 사유.
- SKIP: 카운트만(후보 같은 피처 없으면 과분석 금지).

## 재진입 컨텍스트 (report-only 재구성 — 프로덕션 변경 없음)
- is_reentry, previous_exit_reason, days_since_last_exit, previous_exit_date, same_symbol_reentry_count.
- historical sim 트레이드 leg에서 같은 심볼의 이전 청산을 찾아 재구성. 불가하면 명확히 unavailable.

## 출력
- reports/decision_outcome_score.md (사람용).
- reports/decision_outcome_score.jsonl (기계용, 채점 레코드).

## 마크다운이 답할 것
- BUY 결정이 양의 forward return을 가졌나.
- REJECT가 대체로 옳은 거절이었나.
- 거절로 승자를 놓치고 있나.
- BUY 시그널은 5d/10d/20d/60d 중 언제 더 잘 통하나.
- 결과가 MU/ARM/top3에 집중됐나.
- 미래 데이터 부족으로 unscorable인 레코드가 몇 개인가.
- 전진 증거가 충분한가, 아직 인프라뿐인가.

## 함수
- `compute_forward_outcome(closes, highs, lows, *, horizons, stop, trail, max_hold) -> ForwardOutcome`.
- `compute_reentry_context(symbol, as_of_date, legs) -> ReentryContext`.
- `score_records(records, price_data, legs, *, ...) -> tuple[ScoredRecord]`.
- `summarize_buys/_rejects/_skips`, `build_outcome_report`, `format_outcome_markdown`, `scored_to_jsonl`.
- 러너 `experiments/decision_outcome_score.py` (`python -m experiments.decision_outcome_score [--backfill]`).

## 테스트 (tests/test_decision_outcome.py)
- forward return/MFE/MAE/stop/trail 계산 정확, 미래 바 부족 → unscorable(크래시 없음).
- BUY/REJECT/SKIP 분리 채점, 재진입 필드 채움/unavailable 표기.
- JSONL 레코드 읽기, 베이스라인/유니버스/run_sim 기본값 불변, 브로커/라이브 미사용, real_orders==0.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스, 미래 데이터 누설(forward만), 라이브.
