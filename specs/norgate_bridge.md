# SPEC: norgate_bridge (NDU/Norgate export → historical_sim)

NDU/Norgate가 export한 가격 CSV(폴더 또는 파일)를 `historical_sim`이 먹는 `dict[symbol, OHLCV]`로
바꾸는 **얇은 브리지**. 검증/정규화는 `agents/price_csv.load_price_data_from_frame`를 재사용한다.
NDU 라이브 SDK 연결 아님 — export 파일 입력형(NDU 켜둘 필요 없음).

관련: `agents/price_csv.py`(load_price_data_from_frame, DataAdapterError, close_series),
`agents/historical_sim.py`(price_data 입력).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음.

CRITICAL (fail-closed, 가정 금지): NDU export 포맷이 다르면(필수 컬럼 없음) 추정하지 않고 파일명을 담은
명확한 `DataAdapterError`. 결측/무효 값 행은 price_csv가 드롭(가짜 데이터 안 만듦).

## NDU export 포맷 처리
- NDU 심볼별 CSV는 보통 `symbol` 컬럼이 **없고** 파일명이 심볼(`NVDA.csv`), 컬럼은
  `Date,Open,High,Low,Close,Volume`(대소문자 무시). → `symbol` 컬럼이 없으면 **파일명 stem을 심볼로 주입**.
- `symbol` 컬럼이 있는 단일 long-format 파일도 그대로 처리(주입 안 함).
- 컬럼 매핑·필수컬럼 검증·정규화는 전부 price_csv 재사용.

## 함수
- `load_norgate_csv(path) -> dict`: 단일 파일. symbol 컬럼 없으면 파일명 주입 → load_price_data_from_frame.
- `load_norgate_folder(folder) -> dict`: 폴더의 `*.csv` 각각을 load(파일명=심볼) → 병합. 파일별 검증 오류는
  파일명을 담아 재전파. 폴더 없음/`*.csv` 없음 → DataAdapterError.
- `DataAdapterError`는 price_csv 것을 재노출.

## 테스트
- NDU-style CSV(symbol 컬럼 없음, 파일명=심볼) 로드. 다심볼 폴더 로드. 필수 컬럼 누락 → 파일명 담은 에러.
  historical_sim 구동·성과 산출. real orders 0.

## 비범위
- Norgate SDK 실로드(NDU on), 가격 조정/배당 처리(소스 책임), 전략/시그널 변경, 라이브.
