# SPEC: feature_outcome (피처-성과 분석 — 리포트 전용)

승리 트레이드와 패배 트레이드 사이에서 진입 시점 피처 값이 어떻게 다른지 분석한다. **측정 전용** —
스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않고, 피처를 매수/매도 판단에 쓰지 않는다(아직).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `trade_diag`: TradeDiagnostics(trade_diagnostics.py) — 트레이드 leg(symbol/entry_date/pnl) + best/worst.
- `feature_diag`: FeatureDiagnostics(feature_diagnostics.py) — (symbol, entry_date)별 FeatureSnapshot.

## 승/패 분류
- pnl이 있는(priced) leg만 분석. `pnl > 0` 승리, `pnl < 0` 패배, `pnl == 0` 중립(집계 제외).
- 각 leg를 (symbol, entry_date)로 feature_diag와 조인. 스냅샷 없음/None이면 카운트는 하되 피처 집계서 제외(안전).

## 출력 — FeatureOutcomeReport
- `winners`, `losers`, `neutral`: 건수.
- `numeric_stats`: 피처별 (winner_mean, winner_median, loser_mean, loser_median). 대상:
  momentum_score, return_1m/3m/6m, relative_strength, volume_ratio_20d, atr_pct, distance_from_high.
  값이 None인 피처는 평균/중앙값에서 제외(없으면 통계도 None).
- `flag_stats`: 추세 플래그별 (winner_true_rate, loser_true_rate). 대상: price_above_20ma/50ma, ma20_above_ma50.
- `best_trade_features` / `worst_trade_features`: trade_diag.best/worst 트레이드의 FeatureSnapshot(없으면 None).
- `symbol_summary`: 심볼별 (wins, losses, total_pnl, avg_momentum). total_pnl 내림차순.
- `warnings`: 관찰 경고(판단 아님). 예: 고점 대비 크게 하락한 지점 진입 후 손실
  (distance_from_high ≤ -0.10인 패배 N건), 상대강도 음수 패배, 모멘텀 음수 패배.
- `real_orders_placed == 0` (property).

## run_sim 통합
- 피처 진단 섹션 뒤에 feature outcome 섹션을 출력/저장. 측정 전용.

## 함수
- `compute_feature_outcome(trade_diag, feature_diag) -> FeatureOutcomeReport`.
- `format_feature_outcome(report) -> str`.

## 테스트 (tests/test_feature_outcome.py)
- 승/패 집계가 정확(승자 모멘텀 평균 > 패자 등).
- 플래그 true_rate 집계.
- 스냅샷 없음/None 안전 처리(예외 없음, 카운트 유지).
- best/worst 트레이드 스냅샷 조회.
- 심볼별 요약.
- distance_from_high 경고.
- 트레이드 없음 안전(빈 리포트).
- 입력(trade_diag/feature_diag) 불변 — 매매/veto 안 바뀜.
- real_orders_placed == 0.

## 비범위
- 피처를 이용한 매수/매도/사이징 판단, 스캐너/디시전 변경, 통계적 유의성 검정/ML, 라이브 데이터.
