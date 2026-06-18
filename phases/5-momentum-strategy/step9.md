# Step 9: regime-exits-redesign-v2 (C 강제청산 제거 + D 확정조건 + 청산 늦추기)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §8(레짐 v2 — 분류순서·플레이북 표), §7-2(청산). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002)
- `/algorithms/regime.py`, `/specs/regime.md` (step 1 — classify_regime/RegimePolicy)
- `/algorithms/entry.py` (step 3 — 게이트), `/algorithms/exits.py` (step 4 — ⑤레짐청산/⑦타임스탑/트레일링)

## 작업

헌장 §8 v2를 반영한다. v1에서 레짐 C의 50% 강제청산이 flip-flop churn(거래 119→300, Sharpe 추락)을
일으켰다 → **구조적 교정**. ① C는 강제청산 안 함(신규만 차단) ② D는 확정조건(히스테리시스) ③ B는 고확신만
④ 청산을 너무 빨리 안 하게(트레일 넓게+타임스탑 N 늘림). ⚠️ **구조만 바꾼다 — 숫자 미세튜닝은 편향 없는
데이터(B단계)에서.** 순수 함수.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/regime.md`·`specs/entry.md`·`specs/exits.md` (갱신)

- `classify_regime` 시그니처: vix 스칼라 → **최근 VIX 시리즈(최소 2일)**. D 판정 = `VIX>35 OR (VIX>30 2일 연속)`. 분류 순서는 헌장 §8.
- `RegimePolicy`: **C `exit_fraction_on_break` 0.5 → 0.0**(강제청산 제거), D 1.0(결정론). A/B `allow_new_entry=True`, C/D `False`.
- 진입(B 고확신): regime==B면 더 빡센 게이트 — `trend UP(50d>200d 정렬) AND 상대강도 상위`. A는 기존 게이트.
- 청산 늦추기: 트레일링 ATR 배수 ↑(예 3 → 4, 더 넓게), 타임스탑 N ↑(예 → 15~20 거래일). ⑤는 policy 읽으니 C=0.0이면 자동으로 "강제청산 없음".

### Step B. TEST (Red) — `tests/test_regime.py`·`tests/test_entry.py`·`tests/test_exits.py` (갱신)

- D 확정: 단발 VIX 31(1일) → D 아님(B). VIX>30 2일 연속 → D. VIX 36(1일) → D.
- C 정책: `exit_fraction_on_break == 0.0`(강제청산 없음). D == 1.0.
- B 진입: 약한 추세/상대강도 → B에서 진입 불가(고확신 미달). A에선 통과하던 게 B에선 막히는 케이스.
- 청산: 트레일링 배수·타임스탑 N 새 기본값 반영. C 레짐에서 evaluate_exit가 강제청산 안 함(스탑 히트만).

### Step C. 구현 (Green) — `algorithms/regime.py`·`entry.py`·`exits.py`

step 함수 호출(재구현 금지). 순수 함수(ADR-002).

### Step D. 리팩터

D 확정 로직·고확신 게이트 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_regime.py tests/test_entry.py tests/test_exits.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: D가 확정조건(2일연속/35)으로만 발동하는가? C가 강제청산 안 하는가(churn 제거)? B 고확신 게이트가 작동하는가? 트레일/타임스탑이 늦춰졌는가? 전부 순수 함수·결정론인가?
3. `phases/5-momentum-strategy/index.json`의 step 9 업데이트.

## 금지사항

- C에서 강제청산 복원 금지(churn 원인). D를 단발 VIX 스파이크로 발동시키지 마라(확정조건 필수).
- ⚠️ **편향 든 데이터(NVDA/AMD 불장)에서 숫자를 잘 나오게 미세튜닝하지 마라.** 구조 교정만 — 값은 B단계.
- D 청산을 "50~100% 가능" 같은 재량으로 두지 마라 — 결정론(헌장: 100% 청산).
- regime/entry/exits를 재구현하지 마라(호출). I/O 금지. SPEC/TEST 없이 구현 금지.
