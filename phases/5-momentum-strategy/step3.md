# Step 3: entry-pullback (눌림목 진입 주력 + 돌파 A/B 비교군)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §1(진입 메커니즘 — 눌림목 게이트/트리거), §8(레짐 게이트). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002)
- `/algorithms/signals.py` (step 0 — trend_state/relative_strength), `/algorithms/regime.py` (step 1)
- `/tasks/backtest-engine-prompt.md` (우산 스펙 §4③)

## 작업

헌장 §1의 **눌림목 진입**을 순수 함수로 만든다. 게이트(자격) = 일봉 상승추세 + SPY 상대강도 + 레짐 A/B.
트리거(타이밍) = 추세 유지 중 단기 조정 후 재개. **돌파(Donchian)도 구현**해 백테스트 A/B 비교(헌장이 데이터로 정하라 함). 진입 *판정*만 — 체결 타이밍(다음날 시가)·사이징은 호출부(엔진/sizing).

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/entry.md`

- `EntrySignal`(frozen): `enter: bool`, `reason: str`(eligibility 통과/탈락 사유).
- `pullback_entry(df, *, regime, spy_df, ...) -> EntrySignal`:
  - 게이트: `trend_state==UP` AND `relative_strength(vs SPY)` AND `regime.allow_new_entry`(A/B). 하나라도 실패 → enter=False.
  - 트리거: 추세 내 단기 조정 후 재개(예: 20d선까지 눌렸다가 반등 / RSI 단기 과매도 리셋 후 상향).
- `breakout_entry(df, *, regime, spy_df, lookback=20, ...) -> EntrySignal`: 게이트 동일, 트리거 = 20일 신고가 돌파. (A/B 비교군)
- 미래참조 금지: 판정은 **봉 종가 확정 데이터**만. (체결=다음날 시가는 엔진 담당.)
- 엣지케이스: 데이터 부족/레짐 C·D → enter=False.

### Step B. TEST (Red) — `tests/test_entry.py`

- 상승추세 + 상대강도 + 레짐 A + 눌림 후 반등 → enter=True.
- 게이트 실패(추세 DOWN / SPY보다 약함 / 레짐 C·D) → enter=False(사유 포함).
- breakout: 신고가 돌파 시 enter=True, 아니면 False.
- 데이터 부족 → False.

### Step C. 구현 (Green) — `algorithms/entry.py`

순수 함수(ADR-002). step 0·1 함수를 **호출**(재구현 금지, 단일 진실).

### Step D. 리팩터

게이트·트리거 분리, 눌림목/돌파 공통 게이트 공유.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_entry.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: 게이트(추세+상대강도+레짐)를 모두 AND로 통과해야 진입인가? 레짐 C/D면 진입 불가인가? 판정이 미래참조 없이 봉 종가만 쓰는가? 돌파가 A/B 비교군으로 별도 함수인가?
3. `phases/5-momentum-strategy/index.json`의 step 3 업데이트.

## 금지사항

- 게이트(추세·상대강도·레짐) 없이 트리거만으로 진입시키지 마라(헌장 §1).
- signals/regime을 여기서 재구현하지 마라 — 호출(단일 진실, ADR-002).
- 미래 봉 데이터로 판정하지 마라(look-ahead). I/O·네트워크 금지. SPEC/TEST 없이 구현 금지.
