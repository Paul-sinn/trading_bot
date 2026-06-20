# SPEC: feature_shadow_score (피처 섀도 스코어 — 리포트 전용)

기존 피처 값으로 **투명한 섀도 스코어**를 만들어, 피처 기반 랭킹이 도움이 됐을지(승자가 더 높게
점수화됐는지)를 사후 평가한다. **측정 전용** — 스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고,
이 점수를 매수/매도/사이징에 절대 쓰지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `trade_diag`: TradeDiagnostics(트레이드 leg+pnl, best/worst).
- `feature_diag`: FeatureDiagnostics((symbol,entry_date)별 FeatureSnapshot).

## 투명 스코어(고정 가중치 — 학습/튜닝 아님)
positive(가산): momentum_score, return_1m, return_3m, relative_strength, (volume_ratio_20d−1),
price_above_20ma. return_6m은 보이되 작은 가중치(과대평가 금지). caution(감점): 높은 atr_pct(기준 초과분),
최근 고점 대비 큰 하락(distance_from_high 기준 초과분), missing_fields 개수.
- None 피처는 기여 0으로 안전 처리. 스냅샷 자체가 None이면 점수 불가(unscored, 카운트만).

## 출력 — ShadowScoreReport
- `trades`: ShadowTradeScore(symbol, entry_date, score|None, pnl, is_winner, missing_count) 튜플.
- `num_scored`, `num_unscored`.
- `winner_avg_score`, `loser_avg_score`, `separation`(winner_avg − loser_avg; 한쪽 없으면 None).
- `score_pnl_correlation`: score와 pnl의 단순 상관(샘플 ≥2·분산>0일 때, 아니면 None).
- `top_half_win_rate`, `bottom_half_win_rate`: 점수 중앙 분할 상·하위 승률(랭킹 분리력 요약).
- `best_scored`, `worst_scored`: 점수 최고/최저 트레이드.
- `warnings`: 분리 실패 경고. 예: separation ≤ 0, 상관 음수, 상위 승률 ≤ 하위 승률.
- `real_orders_placed == 0` (property).

## run_sim 통합
- feature outcome 섹션 뒤에 shadow score 섹션 출력/저장. 측정 전용.

## 함수
- `compute_feature_shadow_score(trade_diag, feature_diag) -> ShadowScoreReport`.
- `format_feature_shadow_score(report) -> str`.

## 테스트 (tests/test_feature_shadow_score.py)
- 유효 피처 행에서 점수 계산(모멘텀 높을수록 점수 높음 — 단조성).
- missing_fields/None 값 안전 처리(감점되되 예외 없음).
- 승/패 평균 점수 집계(승자 강할 때 separation>0, 분리 실패 시 경고).
- 스냅샷 None → unscored 안전.
- score-pnl 상관 양수 케이스.
- 리포트 전용: 입력(trade_diag/feature_diag) 불변.
- real_orders_placed == 0.

## 비범위
- 점수를 이용한 매수/매도/사이징, 스캐너/디시전 변경, 가중치 학습/최적화, 라이브 데이터/뉴스/이벤트.
