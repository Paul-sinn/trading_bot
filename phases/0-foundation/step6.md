# Step 6: algo-filters (Layer 2)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (알고리즘 Layer 2 필터 정의)
- `/docs/ADR.md` (ADR-002: 순수 함수 / ADR-005: Claude 센티먼트)
- `/algorithms/signals.py`, `/specs/signals.md`, `/tests/test_signals.py` (step 5 산출물 — 패턴 일관성 참고)

step 5의 시그널 패턴(Signal enum, 순수 함수 스타일)을 그대로 따르라.

## 작업

알고리즘 **Layer 2 (필터링)**을 순수 함수로 구현한다. Layer 1을 통과한 후보를 거른다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/filters.md`

각 필터의 입력/출력/엣지케이스:
- **거래량 급등**: `volume_spike(volume: pd.Series, lookback=20, multiplier=1.5) -> bool` — 최근 거래량이 이동평균×multiplier 초과면 True.
- **ATR 변동성 필터**: `atr_filter(df: pd.DataFrame, period=14, max_atr_pct=0.08) -> bool` — ATR/price 비율이 한도 이하(과도한 변동성 회피)면 통과 True. ATR 계산 포함.
- **뉴스 센티먼트 (Claude)**: `sentiment_filter(symbol: str, provider: SentimentProvider) -> bool` — provider는 인터페이스. `MockSentimentProvider`(결정론적)와 `ClaudeSentimentProvider`(골격, 키 없으면 예외). 이유: 외부 의존은 주입으로 격리, TDD는 mock으로. — ADR-005
- **VIX 체크**: `vix_filter(vix_value: float, max_vix=30.0) -> bool` — VIX가 한도 이하면 통과.
- 통합: `apply_filters(df, symbol, vix, sentiment_provider) -> FilterResult` — 각 필터 통과여부 + 종합 pass/fail.
- 엣지케이스: 데이터 부족, volume 0/결측, ATR 0(가격 불변), VIX 결측, provider 미주입.

### Step B. TEST (Red) — `tests/test_filters.py`

- 거래량 급등 True/False 케이스 (합성 시리즈).
- ATR 한도 초과/이하 케이스.
- `MockSentimentProvider`로 sentiment_filter 동작 검증 (실제 Claude 호출 없음).
- VIX 한도 경계값.
- `apply_filters` 통합: 하나라도 실패하면 전체 fail인지.

### Step C. 구현 (Green) — `algorithms/filters.py`

- pandas/numpy로 ATR/거래량 계산. **`import talib` 금지.**
- 센티먼트는 provider 주입 패턴. `SentimentProvider` 추상 인터페이스 + `MockSentimentProvider` + `ClaudeSentimentProvider`(골격, `NotImplementedError`/명확한 예외).
- 결정론적 필터 함수들은 순수 함수.

### Step D. 리팩터

ATR 계산 헬퍼 분리, provider 주입 구조 정리.

## Acceptance Criteria

```bash
pytest tests/test_filters.py -v
python -c "import pandas as pd, numpy as np; from algorithms.filters import volume_spike; print(volume_spike(pd.Series([100]*19+[1000])))"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 결정론적 필터가 순수 함수인가? 외부 의존(센티먼트)이 주입으로 격리되었는가? — ADR-002/005
   - `talib`를 import하지 않았는가?
   - step 5의 코드 스타일/네이밍과 일관적인가?
3. `phases/0-foundation/index.json`의 step 6을 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`

## 금지사항

- 실제 Claude API를 호출하지 마라. 이유: 키 없음 + 비결정론. `MockSentimentProvider`로 테스트한다.
- `import talib` 하지 마라. 이유: 환경 의존 설치 실패.
- ATR/거래량 계산에서 분모 0·결측을 처리하지 않으면 안 된다. 이유: NaN/예외 폭발.
- SPEC/TEST 없이 구현부터 하지 마라. 이유: ADR-006 위반.
- 기존 테스트(특히 step 5 signals)를 깨뜨리지 마라.
