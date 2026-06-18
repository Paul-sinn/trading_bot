# SPEC: regime (레짐 필터 — SPY 200일선 + VIX → 4레짐 마스터 스위치)

헌장 `docs/STRATEGY.md` §8: 모멘텀을 고른 순간 레짐 필터는 **전략의 일부**다(옵션 아님).
지표 2개 — **SPY vs 200일 이동평균**(시장 추세 방향) + **VIX 수준**(공포/변동성) — 로 4개 레짐을
판별하고, 각 레짐의 플레이북(진입 허용·사이징 배수·레짐 청산 비율)을 결정론적으로 돌려준다.
레짐은 개별 종목 신호 **위에서** 작동하는 마스터 스위치다(종목 신호가 좋아도 레짐 D면 진입 불가).

관련 문서: `docs/STRATEGY.md` §8(4레짐·플레이북), §6(목표·MDD 20% 하드차단), §3(fail-closed),
ADR-002(순수 함수), ADR-006(SDD→TDD), `tasks/backtest-engine-prompt.md` §4②.

CRITICAL: **부수효과 없는 순수 함수.** I/O·네트워크·DB·전역상태·난수 금지. VIX/SPY를 여기서 *조회*하지
않는다 — 입력으로 받는다(I/O는 step6). 입력만으로 출력이 결정된다.

CRITICAL: 임계값(VIX 20/30, 200일선)·사이징 배수는 **시작값**이며 백테스트 튜닝 대상(헌장 §8). 하드코딩
상수로만 박지 말고 파라미터로 노출한다.

## Regime enum

```python
class Regime(str, Enum):
    NORMAL_BULL = "NORMAL_BULL"    # A. 정상 강세
    NERVOUS_BULL = "NERVOUS_BULL"  # B. 불안 강세
    BEARISH = "BEARISH"            # C. 약세/하락추세
    PANIC = "PANIC"                # D. 패닉/위기
```

## RegimePolicy (frozen)

| 필드 | 타입 | 의미 |
|------|------|------|
| `allow_new_entry` | `bool` | 신규 진입 허용 여부 |
| `size_multiplier` | `float` | 사이징 배수(레짐별 공격성) |
| `exit_fraction_on_break` | `float` | 추세/레짐 깨짐 시 청산 비율(헌장 §7-2 ⑤) |

플레이북 (헌장 §8):

| 레짐 | 조건 | allow_new_entry | size_multiplier | exit_fraction_on_break |
|------|------|-----------------|-----------------|------------------------|
| A NORMAL_BULL | SPY > 200d, VIX < 20 | True | 1.0 | 0.0 |
| B NERVOUS_BULL | SPY > 200d, VIX 20~30 | True | 0.5 | 0.0 |
| C BEARISH | SPY < 200d | False | 0.0 | 0.5 |
| D PANIC | VIX > 30 (추세 무관) | False | 0.0 | 1.0 |

## 함수

### `classify_regime(spy_prices, vix_value, *, ma_period=200, vix_elevated=20.0, vix_panic=30.0) -> Regime`

판별 순서 (위에서부터, 먼저 맞는 것):
```
① VIX None/NaN                         → D PANIC   (fail-closed: 위험 불명 = 최대 방어)
② VIX > vix_panic                      → D PANIC   (추세 무관 최우선)
③ SPY 데이터 부족(len < ma_period)      → C BEARISH (상승추세 확인 불가 → 신규 진입 불가)
④ SPY < 200d MA                        → C BEARISH
⑤ SPY ≥ 200d MA & VIX < vix_elevated   → A NORMAL_BULL
⑥ SPY ≥ 200d MA & VIX_elevated ≤ VIX ≤ vix_panic → B NERVOUS_BULL
```
경계값 규칙(결정론):
- VIX = vix_panic(30): `> 30`이 아니므로 PANIC 아님 → (불 구간이면) B.
- VIX = vix_elevated(20): `< 20`이 아니므로 A 아님 → B.
- SPY = 200d MA 정확히: `< MA`가 아니므로 BEARISH 아님 → 불 구간(A/B).

### `policy_for(regime: Regime) -> RegimePolicy`
위 표대로 매핑. (배수·비율은 파라미터 기본값과 일치.)

## 엣지케이스
- **VIX None/NaN** → D PANIC (가장 방어적, fail-closed).
- **SPY 데이터 부족**(200d MA 계산 불가) → C BEARISH (allow_new_entry=False).
- **VIX 정확히 30** → 불 구간이면 B (PANIC 아님).
- **VIX 정확히 20** → B (A 아님).
- **SPY 정확히 200d** → 불 구간 (BEARISH 아님).

## 비범위 (이 step에서 하지 않음)
- VIX/SPY 데이터 조회(I/O) — step6 data-adapter.
- 사이징 실제 적용 — step2 sizing(size_multiplier 소비).
- 포지션 청산 실행 — step4 exits(exit_fraction 소비).
- MDD 20% 하드차단 연동(RiskAgent) — 기존 backend/agents 영역.

---

## step9 갱신 (레짐 v2 — v1 churn 교정, 헌장 §8)

⚠️ v1에서 C의 50% 강제청산이 레짐 flip-flop churn(거래 119→300, Sharpe 1.27→0.85)을 냈다 → 구조 교정.

- **`classify_regime` 시그니처 변경**: `vix_value`(스칼라) → `vix_recent`(스칼라 또는 **최근 VIX 시리즈**).
  D 히스테리시스를 위해 시리즈(최소 2일) 권장. 스칼라도 허용(하위호환 — 단발은 consecutive D 불가, extreme만).
- **D 확정 조건(히스테리시스)**: `VIX > vix_extreme(35)` OR `최근 panic_consecutive_days(2)일 연속 > vix_panic(30)`.
  단발 VIX 스파이크로 패닉청산 방지. 파라미터: `vix_extreme=35.0`, `vix_panic=30.0`(이제 연속 임계), `panic_consecutive_days=2`.
- **분류 순서(헌장 §8)**: ① D(위 확정조건) → ② C(SPY<200d & not D) → ③ B(SPY>200d & VIX≥20 & not D) → ④ A(SPY>200d & VIX<20).
- **RegimePolicy 변경**: **C `exit_fraction_on_break` 0.5 → 0.0**(강제청산 제거 — 기존 포지션은 개별 스탑/트레일로 관리, 추가매수만 금지).
  D는 1.0(결정론 100% 청산) 유지. A(T,1.0,0.0)/B(T,0.5,0.0)/C(F,0.0,**0.0**)/D(F,0.0,1.0).
- VIX None/NaN/빈 시리즈 → PANIC(fail-closed) 유지. 임계값은 시작값 — 편향 없는 데이터(B단계)서 튜닝, 편향 데이터 과튜닝 금지.
