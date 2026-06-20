# SPEC: shadow_whatif (섀도 필터 What-if 분석 — 리포트 전용)

저점수 트레이드를 걸러냈다면 성과가 어떻게 달라졌을지를 **고정 필터**로 추정한다. **측정 전용** —
실 시뮬/스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고, 섀도 점수를 실제 매수/매도/사이징에 절대
쓰지 않는다. 임계값 최적화 없음(과적합 회피) — 분위수/0 같은 자연 경계만 쓴다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `trade_diag`: TradeDiagnostics(인터페이스 일관성용).
- `shadow_report`: ShadowScoreReport — ShadowTradeScore(symbol/entry_date/score/pnl/is_winner).

## 기준선(actual)
- score가 있는 모든 트레이드(필터 없음). 모든 What-if는 이 부분집합 대비 차이를 본다.

## 고정 What-if 필터(최적화 아님)
- `keep-top-quartile`: 점수 상위 25%만 유지(rank/n ≥ 0.75).
- `keep-top-half`: 상위 50%만 유지(≥ 0.5).
- `drop-bottom-quartile`: 하위 25% 제거(≥ 0.25 유지).
- `drop-negative-scores`: score < 0 제거(해당 없으면 actual과 동일 — 안전).
- `drop-{symbol}`: 심볼 1개 제외(leave-one-out), 특히 AMD 제외 — 한 심볼 의존도 점검.

## 시나리오별 리포트 (FilterScenario)
- name, kept_count, removed_count, win_rate, total_pnl, avg_pnl,
  mdd_proxy(entry_date순 누적 실현손익의 최대 낙폭 — 가용 시), total_pnl_diff/avg_pnl_diff(vs actual),
  symbols_kept, symbols_removed, top_symbol, top_symbol_pnl_share(유지 집합 양수손익 비중), concentration_warning.

## 경고
- 개선(또는 성과)이 한 심볼에 집중: 유지 집합 total_pnl의 ≥60%가 한 심볼이면 concentration_warning.
- 한 심볼 의존: `drop-{symbol}`이 actual 대비 total_pnl을 50%+ 떨어뜨리면 report.warnings에 "성과가 {symbol}에 집중".

## 출력 — ShadowWhatIfReport
- `actual`(FilterScenario), `scenarios`(튜플), `warnings`(튜플), `real_orders_placed == 0`(property).

## run_sim 통합
- 버킷 분석 섹션 뒤에 What-if 섹션 출력/저장. 측정 전용.

## 함수
- `compute_shadow_whatif(trade_diag, shadow_report) -> ShadowWhatIfReport`.
- `format_shadow_whatif(report) -> str`.

## 테스트 (tests/test_shadow_whatif.py)
- 필터별 kept/removed/통계 정확.
- actual 트레이드/입력 불변.
- 작은 표본 안전.
- AMD/단일심볼 집중 경고 동작(drop-AMD 의존, kept 집중).
- mdd_proxy 계산.
- real_orders_placed == 0.

## 비범위
- 필터를 실제 매매에 적용, 임계값 최적화/학습, 스캐너/디시전 변경, 라이브 데이터, 정확한 포지션 레벨 MDD.
