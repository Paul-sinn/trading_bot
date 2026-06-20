# SPEC: shadow_bucket_analysis (섀도 스코어 버킷 분석 — 리포트 전용)

섀도 스코어를 사분위 버킷으로 나눠, **높은 점수 버킷이 일관되게 더 좋은 성과를 내는지** 평가한다.
**측정 전용** — 스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고, 점수/버킷을 매수/매도/사이징에
절대 쓰지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `trade_diag`: TradeDiagnostics(인터페이스 일관성용; pnl은 shadow 점수에서 가져옴).
- `shadow_report`: ShadowScoreReport(feature_shadow_score.py) — ShadowTradeScore(symbol/score/pnl/is_winner).

## 버킷 분류
- score가 있는(scored) 트레이드만 대상. score 오름차순 정렬 후 rank로 사분위 배정:
  `idx = min(3, rank*4 // n)` → 0=bottom, 1=lower-middle, 2=upper-middle, 3=top.
- score=None(unscored) 트레이드는 제외(안전). 표본이 작아 일부 버킷이 비어도 안전 처리.

## 버킷별 통계 (BucketStat)
- name(top/upper-middle/lower-middle/bottom), count, win_rate, avg_pnl, median_pnl, total_pnl,
  avg_score, symbols(정렬 unique). 빈 버킷은 통계 None/0.

## 단조성 점검
- 비어있지 않은 버킷을 bottom→top 순서로 보고 avg_pnl·win_rate가 비감소인지 확인.
- `monotonic_avg_pnl`, `monotonic_win_rate`(bool|None), `top_minus_bottom_avg_pnl`(top·bottom 모두 있을 때).
- 경고: 상위 버킷이 하위 버킷을 능가하지 못함(top avg_pnl ≤ bottom), 단조성 위반, 표본 부족(n<4).

## 출력 — ShadowBucketReport
- `buckets`: BucketStat 튜플(top→bottom 순서, 항상 4개).
- `num_scored`, `num_unscored`, `monotonic_avg_pnl`, `monotonic_win_rate`,
  `top_minus_bottom_avg_pnl`, `warnings`, `real_orders_placed == 0`(property).

## run_sim 통합
- shadow score 섹션 뒤에 버킷 분석 섹션 출력/저장. 측정 전용.

## 함수
- `compute_shadow_bucket_analysis(trade_diag, shadow_report) -> ShadowBucketReport`.
- `format_shadow_bucket_analysis(report) -> str`.

## 테스트 (tests/test_shadow_bucket_analysis.py)
- 버킷 배정(상위 점수 → top 버킷).
- 버킷 통계(win_rate/avg/median/total/avg_score) 정확.
- 단조 성과 → 경고 없음 / 비단조 → 경고.
- 작은 표본 안전(예외 없음 + 경고).
- unscored 제외 안전.
- 리포트 전용: 입력(trade_diag/shadow_report) 불변.
- real_orders_placed == 0.

## 비범위
- 버킷/점수로 매수/매도/사이징, 스캐너/디시전 변경, 통계적 검정, 가중치 학습, 라이브 데이터.
