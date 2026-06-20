# SPEC: features (Feature Factory / Momentum Score)

과거 일봉에서 **재사용 가능한 기술적 피처**를 계산하는 **순수 함수** 모듈. 트레이딩 판단을 하지 않는다
(스캐너/디시전/사이징 동작 불변). 지표는 algorithms 기존 순수 함수를 재사용한다(ADR-002 단일 진실 —
SMA는 `signals._sma`, ATR은 `filters._atr`). 새 전략/시그널 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스/이벤트 API
미연결. 피처 계산 전용 — 모듈 안에서 매매 결정을 하지 않는다.

## 입력
- `prices`: 심볼 OHLCV DataFrame(컬럼 `close` 필수; `high/low/close`=ATR, `volume`=거래량비 필요).
- `benchmark`(선택): 벤치마크 종가 Series 또는 OHLCV DataFrame(상대강도용). 없으면 RS=None.
- `symbol`(선택), `price_col`(기본 "close").

## 출력 — FeatureSnapshot (frozen dataclass)
모든 피처는 `float | bool | None`. 계산 불가(데이터 부족/컬럼 결측)는 **None + missing_fields에 이름 추가**
(fail-closed, 예외 아님). 단, 입력 자체가 무효(빈 DF / close 컬럼 없음)면 `FeatureError`.

- `symbol`, `as_of`(마지막 거래일 문자열 또는 None).
- `return_1m` / `return_3m` / `return_6m`: 21 / 63 / 126 거래일 단순수익률(`p[-1]/p[-1-W] - 1`).
- `momentum_score`: 가중 모멘텀 = 0.5·1m + 0.3·3m + 0.2·6m(세 수익률 모두 있어야 계산, 아니면 None).
- `relative_strength`: 벤치마크 대비 초과수익(63d 자산수익 − 벤치수익). 벤치 없음/부족 → None.
- `volume_ratio_20d`: 최근 거래량 / 직전 20일 평균(거래량). volume 결측/부족 → None.
- `atr_pct`: ATR(14) / 현재가. high/low/close 결측/부족 → None.
- `distance_from_high`: `p[-1]/max(p[-252:]) - 1` (0 이하; 최근 고점 대비 위치).
- 추세 플래그(bool|None): `price_above_20ma`, `price_above_50ma`, `ma20_above_ma50`.
- `missing_fields`: 계산 못한 피처 이름 튜플.
- `real_orders_placed == 0` (property).

## 함수
- `compute_features(prices, *, symbol="", benchmark=None, price_col="close", ...) -> FeatureSnapshot`.
- 보조 윈도우 상수: WINDOW_1M=21, WINDOW_3M=63, WINDOW_6M=126, RS_LOOKBACK=63, VOL_LOOKBACK=20,
  ATR_PERIOD=14, HIGH_LOOKBACK=252. MOM_WEIGHTS=(0.5,0.3,0.2).

## fail-closed 규칙
- close NaN 제거 후 길이로 각 피처 가용성 판정. 길이 부족 → 해당 피처 None + missing_fields.
- 빈 DataFrame 또는 price_col 부재 → FeatureError(즉시 명확 실패).
- 모듈은 어떤 매매 상태도 만들지 않는다(읽기 전용).

## 테스트 (tests/test_features.py)
- 픽스처(알려진 가격)에서 1m/3m/6m·모멘텀·거리·플래그가 정확히 계산.
- 짧은 시계열 → 피처 None + missing_fields, 예외 없음(안전).
- 벤치마크 상대강도: 아웃퍼폼 → 양수, 언더퍼폼 → 음수, 벤치 없음 → None.
- 컬럼 결측(volume/high) → 해당 피처만 None, 나머지 정상.
- 무효 입력(빈 DF / close 없음) → FeatureError.
- real_orders_placed == 0.

## 비범위
- 스캐너/디시전/사이징 연동, 전략·시그널 변경, 라이브 데이터/뉴스/이벤트, 피처 정규화/스케일링·ML.
