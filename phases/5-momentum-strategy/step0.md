# Step 0: signals-momentum (시계열 모멘텀·추세 신호 — 기존 다수결 폐기)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** 특히 §0(대전제), §1(1차 엣지·진입), §2(시간프레임). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002: 순수 함수)
- `/algorithms/signals.py`, `/specs/signals.md` (기존 — EMA/RSI/MACD 다수결. **이번에 재설계 대상**)
- `/tasks/backtest-engine-prompt.md` (우산 스펙 §4① 모멘텀 신호)

## 작업

헌장 §1의 **시계열 모멘텀(추세추종)** 신호로 `signals.py`를 재설계한다. ⚠️ 기존 "EMA/RSI/MACD 다수결"은
**폐기**한다 — 헌장 §1이 그 모멘텀+평균회귀 혼합을 명시적으로 정리했다. RSI는 독립 매수신호가 아니라
"상승추세 안의 눌림 타이밍"으로 강등되며, 실제 사용은 step 3(entry)에서 한다. 순수 함수.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/signals.md` (갱신)

- `trend_state(prices, ...) -> TrendState`(UP/DOWN/NEUTRAL): 일봉 중기 추세 판정(예: 가격 vs 50d/200d MA, MA 정렬·기울기).
- `relative_strength(asset_prices, benchmark_prices, lookback) -> bool`(또는 score): SPY 대비 강도(시장보다 강한 종목).
- `generate_signals` 재정의: `overall`은 **추세 기반(모멘텀)** — 다수결 제거. RSI 헬퍼는 남기되 *독립 BULLISH 생성 금지*.
- 엣지케이스: 데이터 부족(워밍업 전) → `NEUTRAL`(안전 기본값).

### Step B. TEST (Red) — `tests/test_signals.py` (갱신)

- 명확한 상승추세 합성데이터 → `UP`, 하락 → `DOWN`, 횡보 → `NEUTRAL`.
- `relative_strength`: SPY보다 강한 시리즈 → True, 약한 → False.
- 데이터 부족 → `NEUTRAL`.
- **회귀(헌장 패러다임 해소)**: RSI 과매도 단독이 `overall` BULLISH를 만들지 않는다.

### Step C. 구현 (Green) — `algorithms/signals.py`

순수 함수(I/O·네트워크·전역상태·난수 금지, ADR-002). pandas/numpy 직접 계산(TA-lib 금지, ADR-008).

### Step D. 리팩터

지표 헬퍼·판정 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_signals.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: 추세 판정이 정확한가? 상대강도가 SPY 대비로 작동하는가? **RSI 단독이 매수신호가 아닌가(헌장 §1)?** 순수 함수인가(ADR-002)?
3. `phases/5-momentum-strategy/index.json`의 step 0을 업데이트한다(completed/error/blocked + summary).

## 금지사항

- **다수결(majority vote) 복원 금지.** RSI를 독립 매수신호로 쓰지 마라(헌장 §1 — 추세추종과 평균회귀를 섞으면 알파 상쇄).
- I/O·네트워크·전역상태·TA-lib 금지(ADR-002/008).
- SPEC/TEST 없이 구현부터 하지 마라(ADR-006).
- **단, 기존 다수결 동작을 검증하던 테스트는 "깨뜨리지 말 것"이 아니라 헌장 신설계에 맞게 *갱신*하라**(의도된 행동 변경). 무관한 테스트는 깨지 마라.
