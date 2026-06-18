# SPEC: exits (R 기반 스케일아웃 청산 래더 — 상태기계)

헌장 `docs/STRATEGY.md` §7-2: 청산이 진입보다 P&L을 더 좌우한다. 철학 = **"손실은 짧게, 이익은 길게"**
(모멘텀=양의 스큐 → 승자를 빡빡한 목표가로 일찍 끊지 마라). 모든 레벨은 **R(초기 리스크)의 배수**로
정의 — 포지션 크기 무관, 백테스트 일관. 이 모듈은 포지션 상태 + 신규 바를 받아 청산 액션을 결정하는
순수 함수 상태기계다.

관련 문서: `docs/STRATEGY.md` §7-2(7단 래더+검증경고)·§8(레짐 청산)·§3(실적 갭)(최상위 권위),
ADR-002(순수 함수), ADR-006(SDD→TDD), `algorithms/regime.py`(step1 policy.exit_fraction),
`algorithms/sizing.py`(step2 R=entry−stop 정의), `tasks/backtest-engine-prompt.md` §4④.

CRITICAL: **부수효과 없는 순수 함수.** I/O·네트워크·DB·전역상태·난수 금지.
CRITICAL: **미래참조 금지** — 현재 바까지만. CRITICAL: regime/sizing 재구현 금지 — 호출(단일 진실).
CRITICAL: ⑤ 레짐 청산은 **재량("대부분") 금지 → % 룰 결정론화**. step1 `policy_for(regime).exit_fraction` 사용.

## 데이터

```python
@dataclass(frozen=True)
class Bar:
    high: float
    low: float
    close: float

@dataclass(frozen=True)
class Position:
    entry_price: float
    initial_stop: float          # 진입 시 ATR 스탑(step2). R = entry - initial_stop.
    qty: float                   # 현재 수량(프랙셔널 허용).
    highest_since_entry: float   # 진입 후 고점 워터마크(트레일링 기준).
    current_stop: float          # 상향돼 온 현재 스탑(초기엔 initial_stop).
    partial_taken: bool          # +partial_take_R 부분익절 이미 했는지(중복 방지).

    @property
    def R(self) -> float:        # 1주당 초기 리스크. <=0이면 R-레벨 규칙 비활성.
        return self.entry_price - self.initial_stop

@dataclass(frozen=True)
class ExitAction:
    sell_fraction: float         # 현재 qty 대비 청산 비율 [0,1]. 0=유지.
    new_stop: float | None       # 상향된 스탑(없으면 None).
    reason: str
```

## `evaluate_exit(position, bar, *, regime, is_pre_earnings=False, days_held=0, atr=None, **params) -> ExitAction`

`highest = max(position.highest_since_entry, bar.high)` (현재 바까지만 갱신).
판정 우선순위(위에서부터, 안전 우선 — 먼저 맞는 규칙 반환):

| # | 규칙 | 조건 | 액션 |
|---|------|------|------|
| ① | 스탑 히트 | `bar.low <= current_stop` | sell **1.0** |
| ⑤D | 레짐 패닉 | `use_regime_exit` & `policy_for(regime).exit_fraction >= 1.0` (D) | sell **1.0** |
| ⑥ | 실적 전(개별주) | `use_pre_earnings` & `is_pre_earnings` | sell `pre_earnings_fraction`(기본 1.0) |
| ⑦ | 타임 스탑 | `use_time_stop` & `days_held >= time_stop_days` & 무진전(`highest < entry + R`) | sell **1.0** |
| ⑤C | 레짐 약세 | `use_regime_exit` & `0 < exit_fraction < 1.0` (C) | sell `exit_fraction`(0.5) |
| ③ | 부분 익절 | `use_partial` & `not partial_taken` & `highest >= entry + partial_take_R·R` | sell `partial_fraction`(기본 1/3) + 스탑 상향 |
| ②④ | 스탑 상향 | 아래 candidate > current_stop | sell **0.0** + `new_stop` |
| — | 유지 | 그 외 | sell 0.0, new_stop None |

**스탑 상향 candidate (②본전 + ④트레일링, 둘 다 "올리기만"):**
```
candidate = current_stop
② if use_breakeven & R>0 & highest >= entry + breakeven_R·R:
      candidate = max(candidate, entry - breakeven_buffer·R)   # 완전 본전 아님 — 구조 아래(헌장 경고1)
④ if use_trailing & atr is not None & highest > entry:
      candidate = max(candidate, highest - atr·trail_atr_mult)  # 수익 엔진(고점 따라 상향)
new_stop = candidate if candidate > current_stop else None
```

**파라미터(전부 백테스트 튜닝 대상, 기본값=시작값):**
- `breakeven_R=1.0`, `breakeven_buffer=0.2`(+1R 후 스탑=entry−0.2R, 완전본전 아님), `partial_take_R=2.0`,
  `partial_fraction=1/3`, `trail_atr_mult=3.0`(넓게), `time_stop_days=10`, `pre_earnings_fraction=1.0`.
- **레이어 토글(step5 A/B 검증용)**: `use_breakeven`, `use_partial`, `use_trailing`, `use_regime_exit`,
  `use_time_stop`, `use_pre_earnings` (기본 모두 True). 베이스라인 = ①스탑 + ④트레일(항상) → 레이어 추가.

## 핵심 불변 (헌장 §7-2)
- 모든 레벨이 **R 배수**(크기 무관). ⑤는 % 룰 결정론(D=1.0/C=0.5, regime policy).
- 본전 스탑은 **완전 본전 아님** — `entry − breakeven_buffer·R`(구조 아래, 휩쏘 방지, 경고1).
- 부분 익절은 **소량**(러너 유지) — `partial_fraction` 기본 1/3.
- 트레일링은 **올리기만**(절대 낮추지 않음) → 수익 엔진. 빡빡한 고정 목표가로 승자 조기 종료 금지.

## 엣지케이스
- `R <= 0`(initial_stop >= entry, 비정상): R-레벨 규칙(②③⑦) 비활성, 스탑히트·레짐·실적만.
- `atr is None`: 트레일링 비활성(본전 상향만 가능).
- 여러 규칙 동시 충족: 위 우선순위로 단일 액션 반환(안전 우선).

## 비범위
- 체결·슬리피지·실제 매도 주문(executor/엔진), 포지션 상태 전이 적용(엔진이 ExitAction을 받아 다음 Position 조립).
- 레짐 *분류*(step1), 진입(step3), 사이징(step2). I/O(step6).

---

## step9 갱신 (청산 늦추기 + C 강제청산 제거, 헌장 §7-2/§8)

- **청산을 너무 빨리 안 하게(승자 태우기)**: 기본값 상향 — `trail_atr_mult` 3.0 → **4.0**(트레일 더 넓게),
  `time_stop_days` 10 → **15**(무진전 정리 늦춤). 값은 시작값 — 편향 없는 데이터서 튜닝.
- **C 강제청산 제거**: ⑤ 레짐청산은 `policy_for(regime).exit_fraction_on_break`를 읽으므로, regime.py에서 C를 0.0으로
  바꾸면 **C에서 evaluate_exit가 강제청산하지 않는다(스탑 히트만 작동)**. D는 1.0 유지. exits.py 로직 자체는 무변경(정책만 변경).
