# Step 2: sizing-kelly (켈리 라벨버그 수정 + 콜드스타트 + 레짐 배수)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §6(목표·MDD governor), §7(사이징), §8(레짐 배수). 충돌 시 헌장이 진실.
- `/tasks/kelly-fix-prompt.md` — **이 step의 상세 스펙(문제 1·2·3).**
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-003: 하드캡 / ADR-002: 순수 함수)
- `/algorithms/sizing.py`, `/specs/sizing.md` (기존), `/algorithms/regime.py` (step 1 — Regime 배수)

## 작업

`tasks/kelly-fix-prompt.md`를 그대로 구현한다. 요지: ① fractional Kelly 라벨버그 수정(비례축소 `fraction`과
상한 `cap` 분리), ② 거래기록 없는 콜드스타트 shrinkage(`effective_kelly_fraction`), ③ MDD governor +
레짐 사이징 배수(켈리 위에 곱하는 별도 레이어). 순수 함수, ADR-003 하드캡 불변.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/sizing.md` (갱신)

- **문제 1**: `kelly_fraction(win_rate, win_loss_ratio, *, fraction=0.5, cap=0.25)` →
  `f_used = clamp(fraction × max(0, f_full), 0, cap)`. docstring "fractional Kelly with a hard cap".
- **문제 2**: `effective_kelly_fraction(win_rate, win_loss_ratio, sample_size, *, fraction=0.5, cap=0.25, prior_fraction=0.0, shrinkage_k=30)` → `w=n/(n+k)`, `f_eff = w·kelly + (1-w)·prior`. `n=0`→prior.
- **문제 3**: 레짐 사이징 배수를 곱하는 훅(`Regime`/`RegimePolicy.size_multiplier` 사용, step 1). 켈리 함수 자체는 순수 유지, 배수는 별도 레이어. `fraction`은 MDD 설계≤15% 목표로 캘리브레이션되는 값임을 명기.
- 불변식(ADR-003): 어떤 경로(켈리·레짐배수·appetite)로도 최종 risk_amount ≤ `equity × max_risk_pct`.

### Step B. TEST (Red) — `tests/test_sizing.py` (갱신)

- full 0.40,fraction0.5,cap0.25→0.20 / full0.10→0.05 / full0.04→0.02. fraction1.0이면 cap만. `[0,cap]` 불변.
- `effective_kelly_fraction`: n=0→prior, n매우큼→≈kelly, 단조성, `[0,cap]` 불변.
- 레짐 배수 적용 후에도 risk_amount ≤ allowed(ADR-003). C/D 배수 0 → 진입 안 함.

### Step C. 구현 (Green) — `algorithms/sizing.py`

순수 함수(ADR-002). `position_size` 호출부 갱신, 하드캡(max_qty 클램프) 유지(2중 안전).

### Step D. 리팩터

켈리·shrinkage·레짐배수 레이어 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_sizing.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: fractional이 *모든* 베팅을 비례축소하는가(작은 베팅도)? n=0이 켈리 미사용인가? 레짐 배수가 켈리 위 별도 레이어인가? 어떤 입력에도 risk_amount가 하드캡 초과 안 하는가(ADR-003)?
3. `phases/5-momentum-strategy/index.json`의 step 2 업데이트.

## 금지사항

- `min(f, cap)`만으로 fractional Kelly라 부르지 마라(라벨버그). 비례축소+상한 분리.
- 콜드스타트에서 경험적 켈리를 신뢰하지 마라(표본 0 → prior).
- ADR-003 하드캡을 우회·완화하지 마라. 레짐 배수가 캡을 *올리지* 못한다.
- I/O·네트워크 금지(ADR-002). SPEC/TEST 없이 구현 금지.
