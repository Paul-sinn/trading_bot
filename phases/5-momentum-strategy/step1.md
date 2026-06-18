# Step 1: regime-filter (SPY 200일선 + VIX → 4레짐 마스터 스위치)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** 특히 §8(레짐 필터 — 4레짐·플레이북), §6(목표·MDD). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002: 순수 함수)
- `/algorithms/signals.py` (step 0 — 같은 모듈 스타일 따르라)
- `/tasks/backtest-engine-prompt.md` (우산 스펙 §4②)

## 작업

헌장 §8의 **4레짐 판별**을 순수 함수로 만든다. 지표 2개(SPY vs 200일선, VIX)로 A/B/C/D 레짐과 각
플레이북 파라미터(진입 허용 여부·사이징 배수)를 결정한다. 레짐은 종목 신호 위에서 작동하는 **마스터 스위치**다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/regime.md`

- `Regime` enum: `NORMAL_BULL`(A) / `NERVOUS_BULL`(B) / `BEARISH`(C) / `PANIC`(D).
- `classify_regime(spy_prices, vix_value, *, ma_period=200, vix_elevated=20.0, vix_panic=30.0) -> Regime`:
  ```
  VIX > vix_panic                    → D PANIC
  SPY < 200d MA                      → C BEARISH
  SPY > 200d MA & VIX < vix_elevated → A NORMAL_BULL
  SPY > 200d MA & VIX 20~30          → B NERVOUS_BULL
  ```
  (D는 추세 무관하게 최우선.)
- `RegimePolicy`(frozen): `allow_new_entry: bool`, `size_multiplier: float`, `exit_fraction_on_break: float`.
  - A: allow=True, mult=1.0, exit=0.0 / B: allow=True, mult=0.5, exit=0.0 / C: allow=False, mult=0.0, exit=0.5 / D: allow=False, mult=0.0, exit=1.0.
- `policy_for(regime) -> RegimePolicy`. 임계값(20/30/200d)·배수는 **시작값**(백테스트 튜닝 대상, 파라미터로 노출).
- 엣지케이스: VIX None/NaN → 보수적으로 가장 방어적(예: D 또는 진입 불가) 처리. SPY 데이터 부족 → 진입 불가.

### Step B. TEST (Red) — `tests/test_regime.py`

- 각 조건 조합 → 올바른 레짐(경계값 포함: VIX=30, SPY=200d 정확히).
- `policy_for`: C/D는 `allow_new_entry=False`, D는 `exit_fraction=1.0`.
- VIX None → 방어적. SPY 데이터 부족 → 진입 불가.

### Step C. 구현 (Green) — `algorithms/regime.py`

순수 함수(ADR-002). pandas/numpy.

### Step D. 리팩터

분류·정책 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_regime.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: D가 추세 무관 최우선인가? C/D에서 신규 진입 불가인가? 임계값이 파라미터로 노출됐는가? 순수 함수인가?
3. `phases/5-momentum-strategy/index.json`의 step 1 업데이트.

## 금지사항

- 임계값(20/30/200d)을 하드코딩 상수로만 박지 마라 — 파라미터로 노출(백테스트 튜닝, 헌장 §8).
- I/O·네트워크·전역상태 금지(ADR-002). VIX/SPY를 여기서 *조회*하지 마라(입력으로 받는다 — I/O는 step 6).
- SPEC/TEST 없이 구현 금지. 기존 테스트 깨지 마라.
