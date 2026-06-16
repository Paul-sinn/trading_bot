# SPEC: scanner_agent (스캐너 에이전트 — 워치리스트 순환 스캔 + 후보 리스트업)

스캐너 에이전트는 워치리스트를 1분 주기로 순환하며 각 심볼의 가격 데이터를 받아 알고리즘
**Layer 1(signals) + Layer 2(filters)** 를 적용해 매수 **후보 종목 리스트**를 만든다.
3레이어 중 Layer 3(sizing)과 Claude 판단은 이 step의 범위가 아니다 — 후속 에이전트로 넘긴다.

관련 문서: PRD(스캐너 = 워치리스트 순환 스캔, 시그널 후보 리스트업, 1분 주기),
ARCHITECTURE(자동매매 루프: 스캐너 → 알고리즘 3레이어), ADR-002(계산은 순수 함수 / I/O는
에이전트 루프), ADR-005(외부 의존은 provider 주입), `specs/agent_base.md`(Agent·AgentRegistry),
`specs/signals.md`(Layer 1), `specs/filters.md`(Layer 2).

CRITICAL: 시그널/필터 지표(EMA/RSI/MACD/ATR/거래량/VIX/센티먼트)를 scanner에서 **재구현하지
않는다**. `algorithms.signals` / `algorithms.filters`의 순수 함수를 **호출만** 한다(단일 진실).

CRITICAL: 외부(Robinhood/Claude) API를 직접 호출하지 않는다. 가격·VIX·센티먼트는 모두 주입된
provider(Mock)로만 들어온다(ADR-001/002).

## 데이터 모델

```python
@dataclass(frozen=True)
class Candidate:
    symbol: str
    signal: SignalResult       # Layer 1 종합 시그널 결과
    filters_passed: bool       # Layer 2 AND 통과 여부
    detail: dict               # 진단용: signal/filter 세부값
```

`SignalResult`는 `algorithms.signals`를, `FilterResult`는 `algorithms.filters`를 재사용한다.

## Provider 인터페이스 (외부 의존 주입)

### `PriceDataProvider`
```python
class PriceDataProvider(Protocol):
    async def get_ohlcv(self, symbol: str) -> pd.DataFrame: ...
```
- 반환 DataFrame 컬럼: `open/high/low/close/volume`.
- 실거래 provider(Robinhood MCP 연동)는 후속 phase. 이 step은 `MockPriceDataProvider`만 사용.

### `MockPriceDataProvider`
- 결정론적 합성 OHLCV를 반환한다(난수·외부 호출 없음).
- 생성 시 심볼별 DataFrame 매핑을 받거나, 추세(상승/하락/횡보) 파라미터로 합성한다.
- 등록되지 않은 심볼은 `KeyError`(provider 예외 경로 검증에 사용 가능).

### (선택) `vix_provider`, `sentiment_provider`
- `vix_provider`: `async def get_vix(self) -> float | None` 또는 미주입 시 기본 VIX 사용.
- `sentiment_provider`: `algorithms.filters.SentimentProvider`(동기 `is_positive(symbol)`).
  미주입 시 `MockSentimentProvider()`(기본 긍정)를 사용.

## ScannerAgent(Agent)

```python
class ScannerAgent(Agent):
    def __init__(self, registry: AgentRegistry, price_provider: PriceDataProvider,
                 watchlist: list[str], *, vix_provider=None, sentiment_provider=None,
                 name: str = "scanner") -> None: ...
    async def scan(self) -> list[Candidate]: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속(IDLE/RUNNING/STOPPED, kill 후 start 거부).
- `scan() -> list[Candidate]`:
  1. `registry.is_killed()`이면 **즉시 빈 리스트** 반환(kill 상태에서 스캔 스킵).
  2. 빈 워치리스트면 빈 리스트.
  3. 각 심볼에 대해 `_evaluate_symbol`을 호출. **한 심볼에서 예외가 나면 그 심볼만 건너뛰고
     나머지 심볼 스캔은 계속한다**(1분 루프가 한 종목 실패로 멈추지 않게 — 격리).
  4. Layer 1 `overall == BULLISH` **이고** Layer 2 `passed == True`인 심볼만 후보에 포함한다.
- `_evaluate_symbol(symbol) -> Candidate | None` (내부 헬퍼, 순수 호출 조합):
  - `df = await price_provider.get_ohlcv(symbol)`.
  - `sig = signals.generate_signals(df)`.
  - `vix = await vix_provider.get_vix()` (있으면) / 기본값.
  - `filt = filters.apply_filters(df, symbol, vix, sentiment_provider)`.
  - BULLISH & passed면 `Candidate(...)`, 아니면 `None`.
- `tick()` — 에이전트 루프 1회:
  1. `scan()` 호출 결과를 `self.latest_candidates`에 저장.
  2. `registry.is_killed()`이면 `scan()`이 빈 결과를 주므로 자연히 스킵된다.

## 엣지케이스

- 빈 워치리스트 → `scan()`은 빈 리스트, `tick()`은 `latest_candidates=[]`.
- provider가 데이터 부족 DataFrame 반환 → signals/filters가 NEUTRAL/False 처리(후보 제외).
- 일부 심볼에서 `get_ohlcv` 예외 → 그 심볼만 제외, 나머지는 정상 스캔.
- `registry.is_killed()` → `scan()` 즉시 빈 리스트(스캔 스킵).
- 약세/필터 탈락 심볼 → 후보 제외(BULLISH & passed 동시 충족만 포함).

## 비범위 (이 step에서 하지 않음)

- Layer 3 sizing, Claude 판단/주문 실행(후속 에이전트).
- 실제 시세/뉴스/VIX API 호출·인증(주입 Mock provider만 사용).
- 주기 스케줄링/asyncio 루프 구동(tick 1회 단위만 제공).
