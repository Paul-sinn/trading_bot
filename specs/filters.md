# SPEC: filters (알고리즘 Layer 2 — 필터링)

알고리즘 3레이어 중 **Layer 2**. Layer 1(signals)을 통과한 후보 종목을 거른다.
거래량 급등 · 변동성(ATR) · 뉴스 센티먼트(Claude) · VIX 4개 필터를 통과한 종목만
다음 레이어(Layer 3 사이징)와 Claude 최종 판단으로 넘긴다.

관련 문서: PRD(알고리즘 3레이어 — Layer 2 필터: 거래량 급등, ATR 변동성 필터, Claude 뉴스 센티먼트, VIX),
ADR-002(알고리즘은 부수효과 없는 순수 함수), ADR-005(Claude는 게이트, 외부 의존은 주입으로 격리),
ADR-006(SDD→TDD).

CRITICAL: 결정론적 필터(`volume_spike` / `atr_filter` / `vix_filter`)는 **부수효과 없는 순수 함수**다.
파일/네트워크/DB/전역상태 접근 금지. 입력만으로 출력(bool)이 결정된다.

CRITICAL: 외부 의존(뉴스 센티먼트 = Claude)은 **provider 주입**으로 격리한다. 알고리즘 코드는
provider 인터페이스에만 의존하고 실제 Claude 호출 로직을 품지 않는다 (ADR-005).

CRITICAL: `import talib` 금지 (C 라이브러리 의존). ATR/거래량 지표는 pandas/numpy로 직접 계산한다.

## FilterResult (통합 결과)

| 필드 | 타입 | 설명 |
|------|------|------|
| `volume` | `bool` | 거래량 급등 필터 통과 여부. |
| `atr` | `bool` | ATR 변동성 필터 통과 여부. |
| `sentiment` | `bool` | 뉴스 센티먼트 필터 통과 여부. |
| `vix` | `bool` | VIX 필터 통과 여부. |
| `passed` | `bool` | 4개 필터 **모두** 통과해야 `True` (AND 결합). |

## SentimentProvider (센티먼트 주입 인터페이스)

```python
@runtime_checkable
class SentimentProvider(Protocol):
    def is_positive(self, symbol: str) -> bool: ...
```

- `MockSentimentProvider`: 결정론적. 생성 시 받은 매핑/기본값으로 응답. 외부 호출·난수 없음. TDD용.
- `ClaudeSentimentProvider`: 실제 Claude 연동 골격. 이 step에서는 로직을 채우지 않고
  호출 시 `NotImplementedError`(키가 있어도 실호출하지 않음). 키 없으면 명확한 예외.

## 함수

### `volume_spike(volume: pd.Series, lookback=20, multiplier=1.5) -> bool`
- 최근 거래량(마지막 값)이 직전 구간(마지막 봉 제외, 최대 `lookback` 봉) 이동평균 × `multiplier`를 **초과**하면 `True`.
- 데이터 길이 < lookback → `False` (판단 불가, 보수적으로 미통과).
- NaN은 dropna로 무시. 정제 후 길이 부족하면 `False`.
- 직전 구간 이동평균이 0(거래량 전부 0)이면 `False` (분모/판단 불가). ZeroDivision 금지.

### `atr_filter(df: pd.DataFrame, period=14, max_atr_pct=0.08) -> bool`
- ATR(평균 True Range)을 `high`/`low`/`close`로 계산.
- True Range = max(high−low, |high−prev_close|, |low−prev_close|). ATR = TR의 Wilder/rolling 평균.
- `ATR / 현재가(close 마지막)` 비율이 `max_atr_pct` **이하**면 통과 `True` (과도한 변동성 회피).
- 데이터 길이 < period+1 → `False`.
- 현재가 0 또는 결측 → `False` (분모 0 회피). ZeroDivision 금지.
- 가격 불변(ATR=0) → 비율 0 ≤ max → `True`.

### `sentiment_filter(symbol: str, provider: SentimentProvider) -> bool`
- `provider.is_positive(symbol)` 결과를 그대로 통과여부로 사용.
- `provider`가 `None`이면 `ValueError` (주입 누락 명시). 이유: 외부 의존을 암묵 기본값으로 숨기지 않는다.

### `vix_filter(vix_value: float, max_vix=30.0) -> bool`
- VIX ≤ `max_vix`이면 통과 `True` (시장 공포 과도하지 않음).
- `vix_value`가 `None`/NaN → `False` (판단 불가, 보수적 미통과).

### `apply_filters(df, symbol, vix, sentiment_provider, *, lookback=20, multiplier=1.5, period=14, max_atr_pct=0.08, max_vix=30.0) -> FilterResult`
- `df`에는 `volume`/`high`/`low`/`close` 컬럼이 있다고 가정. 누락 시 명확한 `KeyError`.
- 4개 필터를 각각 적용하고, **하나라도 실패하면 `passed=False`** (AND 결합).

## 엣지케이스
- **데이터 부족**(필터 기간 미만): 해당 필터 `False`, 예외 없음.
- **volume 0/결측**: dropna 후 판단. 이동평균 0이면 `False`. 예외 없음.
- **ATR 0**(가격 불변): 비율 0 → 통과 `True`. 분모 0 폭발 금지.
- **VIX 결측/None**: `False`.
- **provider 미주입(None)**: `sentiment_filter`/`apply_filters`에서 `ValueError`.
- **컬럼 누락**: `apply_filters`/`atr_filter`에서 명확한 `KeyError`.

## 비범위 (이 step에서 하지 않음)
- Layer 1(signals, step 5), Layer 3(사이징).
- 실제 Claude API 호출 (provider 주입 + Mock으로 대체).
- I/O(데이터 fetch, DB, MCP). 입력 DataFrame/VIX는 호출자가 준비한다.
