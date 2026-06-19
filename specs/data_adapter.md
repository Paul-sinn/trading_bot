# SPEC: data_adapter (무료 일봉 OHLCV 로더 — I/O)

헌장 `docs/STRATEGY.md` §3/§10: v1 백테스트·라이브가 쓸 **일봉 OHLCV 데이터 어댑터**. 무료 일봉 소스
(yfinance/Stooq 등)에서 OHLCV + SPY + VIX를 받아 백테스트 엔진(step5)이 먹는 DataFrame 형식으로
정규화한다. 소스는 **주입형(provider)** — 엔진/테스트는 소스에 묶이지 않는다.

⚠️ 이 모듈은 **I/O라 순수 함수가 아니다** → `agents/`에 둔다(ADR-001: 외부 API·I/O는 backend/agents에만,
ADR-002: algorithms는 순수 유지). 네트워크 호출은 provider 내부에만.

관련 문서: `docs/STRATEGY.md` §3(데이터 소스·생존편향·Robinhood=실행만)·§10(v1), ADR-001/002,
`algorithms/backtest.py`(step5 — 공급할 DataFrame 형식), `agents/scanner.py`(provider 주입 패턴).

CRITICAL: 테스트는 **실제 네트워크 호출 금지**(CI 실패·비결정) — Mock 또는 fetch_fn 주입.
CRITICAL: 실제 SDK(yfinance 등)는 **지연 import** — 미설치 환경에서 모듈 전체가 죽지 않게.
CRITICAL: 시크릿·키를 로그에 노출하지 마라. Robinhood 과거 데이터를 백테스트 소스로 쓰지 마라(헌장 §3).

## 정규화 형식 (백테스트 엔진과 일치)
- 컬럼: `open, high, low, close, volume` (소문자), 전부 `float64`.
- 인덱스: 날짜 오름차순 정렬, 중복 제거. close는 **수정주가**(분할·배당 조정) 우선.
- 결측 행 제거(dropna), 이상치는 호출자 책임(어댑터는 형식 정규화만).

## 인터페이스

### `DailyDataProvider` (Protocol, runtime_checkable)
```python
def get_ohlcv(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame
def get_vix(self, start: str | None = None, end: str | None = None) -> pd.Series
```
- 동기(배치 과거데이터). scanner의 async 라이브 provider와 구분.

### `normalize_ohlcv(raw: pd.DataFrame, price_col_priority=("adj close","close")) -> pd.DataFrame`
- 컬럼명 대소문자 무시 매핑(yfinance 'Open'/'Adj Close' 등 → 소문자). close는 우선순위로 수정주가 채택.
- float64 coerce, 인덱스 정렬·중복 제거·dropna. 출력은 정확히 5개 컬럼.
- 모듈 수준 헬퍼(네트워크 없음). 이상치 검출은 비범위.

### `MockDailyProvider(DailyDataProvider)`
- 생성 시 받은 `{symbol: DataFrame}` 매핑 + vix Series로 응답. **네트워크·난수 없음**(결정론).
- 등록 안 된 심볼 → KeyError. start/end가 주어지면 인덱스 슬라이스(선택).

### `FreeDailyProvider(DailyDataProvider)`
- `__init__(self, *, fetch_fn=None, vix_fetch_fn=None, vix_symbol="^VIX")`.
- `get_ohlcv`: `(fetch_fn or _default_fetch)(symbol, start, end)` → `normalize_ohlcv`. 네트워크는 `_default_fetch`에만.
- `_default_fetch`: **지연 import** yfinance → download. 미설치면 명확한 ImportError(설치 안내).
- `fetch_fn` 주입으로 테스트는 네트워크 없이 정규화 검증.

## 메타 / 생존편향
- `SURVIVORSHIP_WARNING: str` 상수 + `FreeDailyProvider.survivorship_biased: bool = True` 플래그.
- 무료 소스는 상폐종목이 없어 생존편향 내장 → v1 한정, 라이브 전 벤더 재검증 필요(헌장 §3).

## 엣지케이스
- 빈/결측 DataFrame → 정규화 후 빈 DataFrame(예외 없음).
- 컬럼 누락(필수 OHLCV 중 없음) → 명확한 KeyError.
- start/end None → 전체 반환.

## 비범위
- 1시간봉(v2), 실시간 스트리밍, Robinhood 주문/실행(executor), 캐싱 영속화(선택, 메모리 캐시까지만).
- 이상치/스파이크 필터링(호출자/리서치 단계). algorithms 순수성 침범 금지.

## step11 실연동: `NorgateProvider` (생존편향 없는 point-in-time, SDK 실연동)

헌장 §3 "라이브 전 필수: 생존편향 없는 벤더(Norgate급 — 상폐종목 + 시점별 지수편입)로 재검증". `norgatedata`
파이썬 패키지(로컬 NDU 데이터 엔진 접속, 윈도우 전용)를 **지연 import**해 `PointInTimeProvider`를 실구현한다.
미설치/미구독 시 명확한 ImportError 안내. **테스트는 fake SDK 주입(`_sdk()` monkeypatch) — 실 NDU/네트워크 0.**

### Norgate API 매핑 (norgatedata 1.0.74 실측)
- `price_timeseries(symbol, stock_price_adjustment_setting=TOTALRETURN, start_date, end_date, format="pandas-dataframe")`
  → 컬럼 `Open/High/Low/Close/Volume/Turnover/Unadjusted Close/Dividend`, index=`Date`. TOTALRETURN조정 → `Close`가 수정주가.
  `get_ohlcv`는 이 raw를 `normalize_ohlcv`로 흡수("adj close" 없음 → 조정된 "close" 채택).
- `$VIX` 등 지수는 volume이 무의미 → `get_vix`는 `Close`만 뽑아 Series 반환(normalize 안 거침).
- `watchlist_symbols("S&P 500 Current & Past")` → 상폐 포함 후보 풀(생존편향 제거). `watchlists()`로 목록.
- `first_quoted_date(symbol)`→`listed_from`(ISO). `last_quoted_date(symbol)`→ active면 `None`(=`delisted_at None`), 상폐면 상폐일.
- `security_name(symbol)` → 레버리지/인버스 이름 휴리스틱 입력(`2X/3X/Bull/Bear/Ultra/Inverse/Short/Leveraged`).

### 메트릭 산출 (`get_metrics(as_of)`)
후보 풀(워치리스트 + `extra_symbols` 섹터 ETF) 각 심볼에 대해 as_of까지의 가격이력으로:
- `avg_dollar_volume`: 최근 `lookback_days`(기본 63) `Turnover` 평균(=실거래 달러액).
- `atr_pct`: `algorithms.filters._atr`(Wilder, 전략 전체와 동일 정의 재사용) ÷ 마지막 close.
- `is_leveraged_or_inverse`: security_name 토큰 휴리스틱.
선정은 `get_constituents = select_universe(get_metrics(as_of), as_of, min_dollar_volume, atr_pct_band)`(순수, ADR-002).

### 결정/주의
- 기본 워치리스트 `S&P 500 Current & Past`(유동·상폐 포함), `universe_watchlist`/`extra_symbols`로 설정 가능. 섹터 ETF(SMH/XLE/XLF)는 지수 멤버 아님 → `extra_symbols`로 추가.
- `survivorship_biased = False`(상폐 포함). 가격조정 기본 `TOTALRETURN`(설정 가능).
- 무료체험은 히스토리 2년 제한 → 약세장(2018/2022) 풀 재검증은 정식 구독 후. 코드 경로는 동일.
- ⚠️ 비용: get_metrics는 후보 풀 심볼마다 가격이력 1회 조회(연구용 수동 런 — CI 아님). 심볼별 메모리 캐시로 중복 조회 억제.
