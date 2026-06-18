# Step 4: exits-ladder (R 기반 스케일아웃 청산 래더)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §7-2(청산 래더 7단 + 3개 검증경고), §8(레짐 청산), §3(실적 갭 규칙). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002)
- `/algorithms/sizing.py` (step 2 — 초기 ATR 스탑·R 정의), `/algorithms/regime.py` (step 1 — 레짐 청산 %)
- `/tasks/backtest-engine-prompt.md` (우산 스펙 §4④)

## 작업

헌장 §7-2의 **R 기반 청산 래더**를 순수 함수(상태기계)로 만든다. 포지션의 현재 상태 + 신규 바를 받아
청산 액션(전량/부분/유지)을 결정한다. 모든 레벨은 R(초기 리스크) 배수로. 진입보다 P&L을 더 좌우하는 핵심.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/exits.md`

- `Position`(frozen): entry_price, initial_stop, qty, highest_since_entry, R(=entry−initial_stop) 등 상태.
- `ExitAction`(frozen): `sell_fraction: float`(0~1), `new_stop: float | None`, `reason: str`.
- `evaluate_exit(position, bar, *, regime, is_pre_earnings, days_held, ...) -> ExitAction`:
  ```
  ① 초기 스탑: bar.low ≤ stop → sell 1.0
  ② +1R 도달: 손절선 상향 (완전 본전 아닌 직전 스윙로우/구조 아래)
  ③ +1.5~2R: 일부 익절(소량, 예 1/3)
  ④ 이후: 트레일링 스탑(고점−ATR×배수 또는 20d 아래)으로 new_stop 상향
  ⑤ 레짐 깨짐: RegimePolicy.exit_fraction (C: 0.5 / D: 1.0) — **% 룰 결정론**
  ⑥ is_pre_earnings(개별주): 축소/청산
  ⑦ days_held ≥ N & 무진전: 정리
  ```
- 레벨·배수·N은 파라미터(백테스트 튜닝). ⑤는 step 1 RegimePolicy 사용. ⑤ "대부분" 같은 재량 금지 → %.
- 미래참조 금지: 현재 바까지만.

### Step B. TEST (Red) — `tests/test_exits.py`

- 스탑 히트 → sell 1.0. +1R → new_stop 상향. +2R → 부분 익절(소량). 레짐 D → sell 1.0, C → 0.5.
- 실적 전 → 축소/청산. 타임 스탑 → 정리. 트레일링이 고점 따라 올라가는지.
- (레이어 검증은 step 5 백테스트에서 — 여기선 각 액션이 올바른지만.)

### Step C. 구현 (Green) — `algorithms/exits.py`

순수 함수(ADR-002). regime/sizing 호출(재구현 금지).

### Step D. 리팩터

각 청산 규칙을 작은 순수 함수로 분리(레이어별 on/off 가능하게 — step 5 A/B용).

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_exits.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: 모든 레벨이 R 배수인가? ⑤가 재량 아닌 % 룰인가(D=1.0/C=0.5)? 트레일링이 수익 엔진으로 작동하는가? 본전 스탑이 "완전 본전"이 아니라 구조 아래인가(헌장 경고1)? 미래참조 없는가?
3. `phases/5-momentum-strategy/index.json`의 step 4 업데이트.

## 금지사항

- ⑤에 "대부분" 같은 재량 표현 금지 — % 룰로 결정론화(봇).
- 빡빡한 고정 목표가로 승자를 일찍 끊지 마라(헌장: 모멘텀=양의 스큐, 트레일링이 태움).
- regime/sizing 재구현 금지(단일 진실). 미래참조·I/O·네트워크 금지. SPEC/TEST 없이 구현 금지.
- 청산 규칙을 한 덩어리로 묶지 마라 — 레이어별 on/off 가능하게 분리(step 5 A/B 검증 필요).
