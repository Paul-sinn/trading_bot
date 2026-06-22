# SPEC: daily_shadow (일간 섀도 리포트 / 전진 원장 러너 — 실험/리포트 전용)

매일 한 번 돌려 전진 검증 레코드를 누적하는 report-only 워크플로. (1) 최신 거래일 결정 로그 생성, (2)
기계용 결정 레코드를 ID 중복 없이 append, (3) 충분한 미래 데이터가 생긴 과거 레코드만 채점(미성숙
horizon은 pending), (4) 간결한 사람용 일간 리포트 작성. 기존 두 러너(signal_decision_log,
decision_outcome_score)를 오케스트레이션할 뿐 — 스캐너/디시전/RiskGate/베이스라인을 바꾸지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 진입/청산/유니버스/베이스라인 미변경.

## 레코드 ID / 멱등 append
- record_id = `date|symbol|decision` (안정 ID).
- 결정 원장(signal_decision_log.jsonl): ID 미존재 행만 append(여러 번 실행해도 중복 없음).
- 결과 원장(decision_outcome_score.jsonl): 결과는 시간이 지나며 성숙하므로 ID로 upsert(최신 결과로 갱신).

## pending vs matured
- 레코드 날짜 D, 데이터 끝 L. forward 바 부족(미래가 아직 안 옴) → 해당 horizon은 **pending**(실패 아님).
- as_of가 데이터에 없으면 unscorable(별개). returns[h]=None & scorable → pending. 값 있으면 matured.
- newly matured = 이번 실행에서 처음 값이 생긴 (id, horizon)(기존 결과 원장 대비 diff).

## 일간 마크다운 (reports/daily_shadow_report.md)
- report date, BUY/REJECT/SKIP 카운트, RiskGate veto 카운트.
- BUY 표(planned entry next-bar-limit/0.03, exit 15/20/60).
- REJECT/SKIP 상위 사유.
- 1/5/10/20/60d newly matured 요약(카운트 + 평균 return) + pending 카운트.
- 재진입 요약(가능하면), BUY가 MU/ARM/top3에 쏠리면 집중 경고.
- real_orders_placed = 0 명시.

## 함수
- `record_id(rec) -> str`, `merge_decision_ledger(existing, new) -> (merged, added)` (dedupe append).
- `upsert_outcome_ledger(existing, new) -> merged` (ID upsert).
- `count_matured/_pending(scored, horizons, decision)`, `count_newly_matured(existing_by_id, scored, horizons)`.
- `build_daily_shadow(...) -> DailyShadowReport`, `format_daily_shadow_markdown(report) -> str`.
- 러너 `experiments/daily_shadow_report.py` (`python -m experiments.daily_shadow_report [--date]`).

## 테스트 (tests/test_daily_shadow.py)
- 재실행 시 결정 원장 중복 append 없음, 결과 원장 upsert.
- pending horizon은 pending으로 표기, mature horizon은 채점.
- newly matured 카운트, 베이스라인/유니버스/run_sim 기본값 불변, 브로커/라이브 미사용, real_orders==0.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스, 미래 누설, 라이브.
