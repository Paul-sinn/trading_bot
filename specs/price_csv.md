# SPEC: price_csv (Norgate-export / CSV → historical_sim price_data)

Norgate export(또는 CSV) long-format 가격 데이터를 `historical_sim`이 먹는 `dict[symbol, OHLCV DataFrame]`
로 바꾸는 **얇은 어댑터**. 기존 `agents/data_adapter.normalize_ohlcv`를 재사용한다.

관련: `agents/data_adapter.py`(normalize_ohlcv), `agents/historical_sim.py`(price_data 입력).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 튜닝 없음. NDU 라이브 연결 아님 — 파일/프레임 입력형.

CRITICAL (fail-closed, 가정 금지): 필수 컬럼이 하나라도 없으면 즉시 에러(컬럼 추정/기본값 없음).
값(가격/날짜)이 결측/무효인 행은 드롭(가짜 데이터 안 만듦). 유효 행이 0인 심볼은 제외(historical_sim이
자연 제외 → 거래 안 됨).

## 기대 입력 (long-format)
컬럼: `symbol, date, open, high, low, close, volume` (대소문자 무시). 여러 심볼이 한 파일에 섞여도 됨.

## 함수
- `load_price_data_from_frame(df) -> dict[str, pd.DataFrame]`:
  필수 컬럼 검증(없으면 `DataAdapterError`) → date 파싱(무효 드롭) → OHLCV 숫자 강제(무효→NaN→드롭) →
  심볼별 그룹 → date 인덱스 → `normalize_ohlcv` → 유효 심볼만 dict.
- `load_price_data_from_csv(path) -> dict`: `pd.read_csv` 후 위 함수. 파일 없음 → `DataAdapterError`.
- `close_series(price_data, symbol) -> pd.Series`: 심볼의 close 시리즈(없으면 `DataAdapterError`).
- `DataAdapterError(Exception)`.

## 테스트
- 유효 CSV → historical_sim 구동·성과 산출. 필수 컬럼 누락 → DataAdapterError. 가격/날짜 결측 행 안전 드롭.
  real orders 0.

## 비범위
- Norgate SDK 실로드(NDU), 가격 조정/배당/분할 처리(소스 책임), 전략/시그널 변경.
