# SPEC: feature_diagnostics (피처 진단 — 리포트 전용)

진입(트레이드)·후보 시점의 `FeatureSnapshot`(algorithms/features.py) 값을 진단에 노출한다. **측정 전용** —
스캐너/디시전/사이징/RiskGate 동작을 바꾸지 않는다. 피처를 매수/매도 판단에 쓰지 않는다(아직).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

## 입력
- `multiday`: 시뮬 결과(트레이드 entry symbol/date 추출용). 기존 `compute_trade_diagnostics(...).trades`를
  재사용해 (symbol, entry_date)를 얻는다(트레이드 페어링 단일 진실).
- `price_data`: {symbol: OHLCV DataFrame}. 피처 계산 원천.
- `benchmark_prices`(선택): 상대강도용 벤치마크 종가 Series.
- `source_trades`(선택, 테스트용): trades를 직접 주입(각 객체 .symbol/.entry_date). 주면 multiday 미사용.

## point-in-time (미래참조 금지)
각 진입 (symbol, entry_date)에 대해 `price_data[symbol].loc[:entry_date]`로 슬라이스해 그 시점까지의
데이터로만 피처를 계산한다(벤치마크도 동일 슬라이스). entry_date가 None이면 전체 시계열 + note.

## 출력 — FeatureDiagnostics
- `rows`: FeatureRow 튜플. (symbol, entry_date) 중복은 1행으로 dedupe.
  - FeatureRow: `symbol`, `context_date`, `snapshot`(FeatureSnapshot | None), `note`(str | None).
  - 가격 데이터 없음/슬라이스 비어 계산 불가 → snapshot=None, note에 사유(예외 아님, fail-closed).
- `real_orders_placed == 0` (property).

## 노출 피처(스냅샷에서)
momentum_score, return_1m/3m/6m, relative_strength, volume_ratio_20d, atr_pct, distance_from_high,
추세 플래그(price_above_20ma/50ma, ma20_above_ma50), missing_fields.

## run_sim 통합
- 매매 진단 뒤에 feature 진단 섹션을 출력/저장(항상 — 이벤트 유무 무관). 측정 전용.
- price_data/benchmark는 데이터 폴더에서 로드(simulate와 동일 유니버스 규칙). 로드 실패는
  feature 섹션을 비우되 전체 실행을 막지 않는다(리포트 전용, fail-safe).

## 함수
- `compute_feature_diagnostics(multiday, price_data, *, benchmark_prices=None, source_trades=None) -> FeatureDiagnostics`.
- `format_feature_diagnostics(diag) -> str`.

## 테스트 (tests/test_feature_diagnostics.py)
- 유효 픽스처: 트레이드 진입에 피처(momentum_score/returns/flags) 계산됨.
- 데이터 부족: snapshot.missing_fields에 미계산 피처 보고(예외 없음).
- 가격 데이터 없는 심볼: snapshot=None + note(안전).
- point-in-time: 슬라이스 결과 as_of == entry_date(미래참조 없음).
- 진단이 trade_log/veto를 바꾸지 않음(불변).
- real_orders_placed == 0.

## 비범위
- 피처를 이용한 매수/매도/사이징 판단, 스캐너/디시전 변경, 라이브 데이터/뉴스/이벤트, 피처 스케일링/ML.
