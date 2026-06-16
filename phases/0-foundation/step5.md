# Step 5: algo-signals (Layer 1)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (알고리즘 3레이어 정의)
- `/docs/ADR.md` (ADR-002: 알고리즘은 부수효과 없는 순수 함수)
- `/algorithms/__init__.py` (step 0 산출물)

## 작업

알고리즘 **Layer 1 (시그널 생성)**을 순수 함수로 구현한다. 외부 I/O·전역상태 금지. 입력은 가격 DataFrame, 출력은 시그널.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/signals.md`

각 지표의 입력/출력/엣지케이스 정의:
- **EMA 크로스(9/21)**: `ema_cross(prices: pd.Series) -> Signal` — 단기(9)가 장기(21)를 상향 돌파=`BULLISH`, 하향=`BEARISH`, 그 외 `NEUTRAL`.
- **RSI(14)**: `rsi_signal(prices: pd.Series, period=14, overbought=70, oversold=30) -> Signal` — ≤30 `BULLISH`(과매도 반등), ≥70 `BEARISH`(과매수), 그 외 `NEUTRAL`.
- **MACD 히스토그램**: `macd_signal(prices: pd.Series, fast=12, slow=26, signal=9) -> Signal` — 히스토그램 양전환 `BULLISH`, 음전환 `BEARISH`.
- 통합: `generate_signals(df: pd.DataFrame) -> SignalResult` — 위 3개를 종합한 결과 객체.
- `Signal` enum: `BULLISH | NEUTRAL | BEARISH`.
- 엣지케이스: 데이터 길이 부족(< period) → `NEUTRAL` 또는 명확한 예외, NaN 포함, 가격 전부 동일(분모 0 방지), 빈 시리즈.

### Step B. TEST (Red) — `tests/test_signals.py`

결정론적 입력으로 작성 (mock 불필요 — 순수 함수):
- 명백한 상승 추세 시리즈 → EMA/MACD `BULLISH`.
- 과매수/과매도 구간 합성 시리즈 → RSI 신호 검증.
- 데이터 부족/NaN/동일가격 엣지케이스에서 예외 없이 처리되는지.
- `generate_signals` 통합 결과 타입/필드.

### Step C. 구현 (Green) — `algorithms/signals.py`

- pandas/numpy로 직접 계산한다. **`import talib` 금지** (환경 의존). EMA/RSI/MACD를 pandas `ewm` 등으로 구현.
- 모든 함수는 순수 함수: 입력만으로 출력 결정, 부수효과 없음.

### Step D. 리팩터

지표 계산 헬퍼(ema, rsi, macd 계산부)와 시그널 판정부를 분리. 테스트 유지.

## Acceptance Criteria

```bash
pytest tests/test_signals.py -v
python -c "import pandas as pd, numpy as np; from algorithms.signals import generate_signals; print(generate_signals(pd.DataFrame({'close': np.linspace(100,120,60)})))"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - `algorithms/signals.py`가 순수 함수인가 (I/O·전역상태 없음)? — ADR-002
   - `talib`를 import하지 않았는가?
   - 엣지케이스(데이터 부족/NaN)를 처리하는가?
3. `phases/0-foundation/index.json`의 step 5를 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`

## 금지사항

- `import talib` 하지 마라. 이유: C 라이브러리 의존으로 설치 실패. pandas/numpy로 구현.
- 함수 안에서 파일/네트워크/DB에 접근하지 마라. 이유: 순수 함수 원칙(ADR-002), 테스트 불가능해짐.
- SPEC/TEST 없이 구현부터 하지 마라. 이유: ADR-006 위반.
- RSI 계산에서 분모 0(가격 변동 없음)을 처리하지 않으면 안 된다. 이유: ZeroDivision/NaN 폭발.
- 기존 테스트를 깨뜨리지 마라.
