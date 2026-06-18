# SPEC: signals (알고리즘 Layer 1 — 시계열 모멘텀·추세 신호)

알고리즘 3레이어 중 **Layer 1**. 일봉 가격 시계열에서 **중기 추세(시계열 모멘텀)**를 판정하고,
SPY 대비 상대강도를 측정한다.

> ⚠️ **재설계(2026-06-17, phase 5 step0)**: 기존 "EMA(9/21) 크로스 + RSI(14) + MACD 다수결"은 **폐기**한다.
> 헌장 `docs/STRATEGY.md` §1이 그 모멘텀+평균회귀 혼합을 명시적으로 정리했다:
> - 매수 방향(트리거)은 **일봉 중기 추세(시계열 모멘텀)** 단일 책임으로 본다. 다수결 없음.
> - **RSI는 독립 매수신호가 아니다.** "상승추세 안의 눌림 타이밍"으로 강등되며, 실제 소비는 step 3(entry)에서 한다.
>   이 레이어는 RSI **원시값만** 제공한다(Signal 생성 금지).
> - 상대강도(SPY 대비)는 "시장보다 강한 종목"을 거르는 **보조 필터**다. 게이트 조합(추세+상대강도+레짐)은 step 3에서 한다.

관련 문서: `docs/STRATEGY.md` §0/§1/§2 (최상위 권위), ADR-002(순수 함수), ADR-008(TA-lib 금지),
ADR-006(SDD→TDD), `tasks/backtest-engine-prompt.md` §4① 모멘텀 신호.

CRITICAL: 이 모듈은 **부수효과 없는 순수 함수**다. 파일/네트워크/DB/전역상태/난수 접근 금지.
입력(가격 Series/DataFrame)만으로 출력이 결정된다.

CRITICAL: `import talib` 금지(C 라이브러리 의존). 지표는 pandas/numpy로 직접 계산한다.

## Enums

```python
class Signal(str, Enum):       # 다운스트림(scanner/decision) 호환 유지
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"

class TrendState(str, Enum):   # 일봉 중기 추세 판정
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"
```

## SignalResult (통합 결과)

| 필드 | 타입 | 설명 |
|------|------|------|
| `trend` | `TrendState` | 일봉 중기 추세(UP/DOWN/NEUTRAL). |
| `overall` | `Signal` | **추세 기반 종합.** UP→BULLISH, DOWN→BEARISH, NEUTRAL→NEUTRAL. **다수결·RSI 영향 없음.** |
| `relative_strength` | `bool \| None` | SPY(벤치마크) 대비 강한가. 벤치마크 미제공 시 `None`. |
| `rsi` | `float \| None` | 최신 RSI 원시값(step3 눌림 타이밍용). 워밍업 전이면 `None`. **이 값으로 매수신호를 만들지 않는다.** |

## 함수

### `trend_state(prices: pd.Series, fast=50, slow=200) -> TrendState`
일봉 중기 추세를 MA 정렬로 판정한다(헌장 §1 "일봉 상승추세 예: 50d/200d 위").
- `UP`  : 최신 종가 > MA(fast) > MA(slow) (상승 정렬).
- `DOWN`: 최신 종가 < MA(fast) < MA(slow) (하락 정렬).
- 그 외 → `NEUTRAL`.
- 데이터 길이 < slow (워밍업 전) → `NEUTRAL` (안전 기본값).
- MA는 단순이동평균(SMA, `rolling(window).mean()`). min_periods=window — 부분 윈도우는 NaN→NEUTRAL.

### `relative_strength(asset_prices, benchmark_prices, lookback=63) -> bool | None`
SPY 대비 상대강도(시장보다 강한 종목만, 헌장 §1 보조 필터).
- 두 시리즈는 호출자가 같은 시점으로 정렬해 전달한다고 가정(말단이 동일 시점).
- `asset_ret = asset[-1]/asset[-1-lookback] - 1`, `bench_ret` 동일.
- `asset_ret > bench_ret` → `True`, 아니면 `False`.
- 둘 중 하나라도 길이 ≤ lookback (워밍업 전) → `None` (판정 불가, 안전).

### `rsi_value(prices: pd.Series, period=14) -> float | None`
최신 RSI **원시값**만 반환한다(Signal 아님 — step3 타이밍 전용).
- 데이터 길이 < period+1 → `None`.
- 가격 변동 없음(분모 0): 손실·이득 모두 0 → RSI=50.0. ZeroDivision 금지.

### `generate_signals(df, benchmark=None, price_col="close", *, fast=50, slow=200, rsi_period=14, rs_lookback=63) -> SignalResult`
- `trend = trend_state(df[price_col], fast, slow)`.
- `overall`: UP→BULLISH / DOWN→BEARISH / NEUTRAL→NEUTRAL. **다수결·RSI 미사용.**
- `rsi = rsi_value(df[price_col], rsi_period)` (원시값, 정보 제공용).
- `relative_strength`: `benchmark`(가격 Series 또는 동일 `price_col` 가진 DataFrame)가 주어지면 계산, 없으면 `None`.
- `price_col`이 df에 없으면 `KeyError`.

## 엣지케이스
- **빈 시리즈/DataFrame**: `trend=NEUTRAL`, `overall=NEUTRAL`, `rsi=None`, 예외 없음.
- **데이터 길이 부족**(slow 미만): `trend=NEUTRAL` (예외 없음).
- **NaN 포함**: `dropna`로 무시하고 계산. 전부 NaN이면 `NEUTRAL`.
- **모든 가격 동일**(변동 0): trend `NEUTRAL`(MA 정렬 동률), rsi 50.0. 분모 0 폭발 금지.
- **명백한 상승추세**(종가>50d>200d): `trend=UP`, `overall=BULLISH`.
- **회귀(헌장 패러다임 해소)**: RSI 과매도 단독은 **절대** `overall=BULLISH`를 만들지 않는다(추세가 단일 진실).

## 비범위 (이 step에서 하지 않음)
- 진입 타이밍 조합(눌림목 트리거) — step 3(entry-pullback).
- 레짐 필터 — step 1(regime-filter).
- 1시간봉 정밀화(v2, 헌장 §10).
- I/O(데이터 fetch, DB, MCP). 입력은 호출자가 준비한다.
