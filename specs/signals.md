# SPEC: signals (알고리즘 Layer 1 — 시그널 생성)

알고리즘 3레이어 중 **Layer 1**. 가격 시계열에서 기술적 지표(EMA/RSI/MACD)를 계산해
방향성 시그널을 만든다.

관련 문서: PRD(알고리즘 3레이어 — Layer 1 시그널: EMA 크로스 9/21, RSI 14, MACD),
ADR-002(알고리즘은 부수효과 없는 순수 함수), ADR-006(SDD→TDD).

CRITICAL: 이 모듈은 **부수효과 없는 순수 함수**다. 파일/네트워크/DB/전역상태 접근 금지.
입력(가격 DataFrame/Series)만으로 출력(시그널)이 결정된다.

CRITICAL: `import talib` 금지 (C 라이브러리 의존). 지표는 pandas/numpy로 직접 계산한다.

## Signal enum

```python
class Signal(str, Enum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
```

## SignalResult (통합 결과)

| 필드 | 타입 | 설명 |
|------|------|------|
| `ema` | `Signal` | EMA 크로스(9/21) 시그널. |
| `rsi` | `Signal` | RSI(14) 시그널. |
| `macd` | `Signal` | MACD 히스토그램 시그널. |
| `overall` | `Signal` | 3개 시그널의 다수결 종합. |

다수결 규칙: BULLISH 개수 > BEARISH 개수 → `BULLISH`, 반대면 `BEARISH`, 동률 → `NEUTRAL`.

## 함수

### `ema_cross(prices: pd.Series, fast=9, slow=21) -> Signal`
- 단기 EMA(fast)가 장기 EMA(slow)를 **상향 돌파**(직전 봉 fast≤slow → 현재 봉 fast>slow) → `BULLISH`.
- **하향 돌파**(직전 fast≥slow → 현재 fast<slow) → `BEARISH`.
- 그 외(돌파 없음) → `NEUTRAL`.
- 데이터 길이 < slow+1 → `NEUTRAL`.

### `rsi_signal(prices: pd.Series, period=14, overbought=70, oversold=30) -> Signal`
- 최신 RSI ≤ oversold → `BULLISH` (과매도 반등 기대).
- 최신 RSI ≥ overbought → `BEARISH` (과매수).
- 그 외 → `NEUTRAL`.
- 데이터 길이 < period+1 → `NEUTRAL`.
- 가격 변동 없음(분모 0): 손실/이득 모두 0이면 RSI=50 처리 → `NEUTRAL`. ZeroDivision 금지.

### `macd_signal(prices: pd.Series, fast=12, slow=26, signal=9) -> Signal`
- 히스토그램(MACD선 − 시그널선)이 **음→양 전환** → `BULLISH`.
- **양→음 전환** → `BEARISH`.
- 그 외(부호 유지) → `NEUTRAL`.
- 데이터 길이 < slow+signal → `NEUTRAL`.

### `generate_signals(df: pd.DataFrame, price_col="close") -> SignalResult`
- `df[price_col]` 시리즈를 위 3개 함수에 통과시켜 `SignalResult` 반환.
- `price_col`이 없으면 명확한 `KeyError`/`ValueError`.

## 엣지케이스
- **빈 시리즈/DataFrame**: 모든 시그널 `NEUTRAL`, 예외 없음.
- **데이터 길이 부족**(지표 기간 미만): 해당 시그널 `NEUTRAL`, 예외 없음.
- **NaN 포함**: NaN은 dropna로 무시하고 계산. 전부 NaN이면 `NEUTRAL`.
- **모든 가격 동일**(변동 0): EMA 차이 0(NEUTRAL), RSI=50(NEUTRAL), MACD 히스토그램 0(NEUTRAL). 분모 0 폭발 금지.
- **명백한 상승 추세**: EMA `BULLISH` 가능, MACD `BULLISH` 가능.

## 비범위 (이 step에서 하지 않음)
- Layer 2(필터), Layer 3(사이징).
- I/O(데이터 fetch, DB, MCP). 입력 DataFrame은 호출자가 준비한다.
