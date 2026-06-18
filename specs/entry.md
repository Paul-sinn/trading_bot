# SPEC: entry (눌림목 진입 주력 + 돌파 A/B 비교군)

헌장 `docs/STRATEGY.md` §1: 진입은 **눌림목(상승추세 중 조정 진입)** 주력. 돌파(고점매수)는 스탑이 멀고
가짜 돌파 휩쏘가 커 불리하나, v1 백테스트에서 눌림목과 A/B 비교해 데이터로 최종 확정한다(§1). 이 모듈은
진입 **판정**만 한다 — 체결 타이밍(다음날 시가)·사이징은 호출부(엔진/sizing)가 담당.

관련 문서: `docs/STRATEGY.md` §1(눌림목 게이트/트리거)·§8(레짐 게이트)(최상위 권위), ADR-002(순수 함수),
ADR-006(SDD→TDD), `algorithms/signals.py`(step0 trend_state/relative_strength/rsi_value),
`algorithms/regime.py`(step1 policy_for), `tasks/backtest-engine-prompt.md` §4③.

CRITICAL: **부수효과 없는 순수 함수.** I/O·네트워크·DB·전역상태·난수 금지.
CRITICAL: **미래참조(look-ahead) 금지.** 판정은 봉 **종가 확정 데이터**만 사용한다. 미래 봉으로 판정하지 않는다.
CRITICAL: step0/step1을 **재구현하지 않는다** — 호출한다(단일 진실). signals/regime 로직 복제 금지.

## 진입 구조 (헌장 §1)
```
게이트(자격) : ① trend_state == UP          ← 일봉 중기 상승추세(모멘텀 자격)
              ② relative_strength(vs SPY)   ← 시장보다 강한 종목
              ③ regime.allow_new_entry      ← 레짐 A/B만(C/D는 진입 불가, 마스터 스위치)
              세 게이트를 모두 AND로 통과해야 트리거 평가로 간다.
트리거(타이밍): 눌림목 = 추세 유지 중 단기 조정 후 재개 (20d선 눌림 후 반등)
              돌파   = 20일 Donchian 신고가 돌파 (A/B 비교군)
```
- ⚠️ 게이트 없이 트리거만으로 진입 금지(헌장 §1). RSI 과매도 단독도 진입 아님 — 게이트 통과가 선행.

## EntrySignal (frozen)

| 필드 | 타입 | 설명 |
|------|------|------|
| `enter` | `bool` | 진입 판정. 게이트 AND 트리거 모두 충족 시 True. |
| `reason` | `str` | 통과/탈락 사유(어느 게이트·트리거에서 갈렸는지). |

## 함수

### `pullback_entry(df, *, regime, spy_df, price_col="close", fast=50, slow=200, rs_lookback=63, short_ma=20, window=5, touch_tol=0.0) -> EntrySignal`
- **게이트**(공통): `trend_state(close,fast,slow)==UP` AND `relative_strength(close, spy_close, rs_lookback) is True`
  AND `policy_for(regime).allow_new_entry`. 하나라도 실패 → `enter=False`, reason에 첫 실패 게이트.
- **트리거**(눌림목): 최근 `window` 봉 내 직전 봉들 중 하나가 `short_ma`선 근처/아래로 눌림
  (`close <= ma_short × (1+touch_tol)`) **그리고** 마지막 봉이 재개(`close[-1] > close[-2]` 그리고 `close[-1] >= ma_short[-1]`).
- 게이트 통과 + 트리거 충족 → `enter=True`. 게이트 통과했으나 트리거 미충족(예: 눌림 없는 단조 상승) → `enter=False`.

### `breakout_entry(df, *, regime, spy_df, price_col="close", fast=50, slow=200, rs_lookback=63, lookback=20) -> EntrySignal`
- **게이트**: 위와 동일(공통 게이트 공유).
- **트리거**(돌파): 최신 종가가 직전 `lookback` 봉의 최고 종가(Donchian 상단)를 초과 → 신고가 돌파.
- A/B 비교군. 게이트+트리거 → `enter=True`.

## 엣지케이스
- 데이터 부족(trend NEUTRAL / relative_strength None / 트리거 계산 불가) → `enter=False`(예외 없음).
- 레짐 C·D → `allow_new_entry=False` → `enter=False`(트리거 평가 안 함).
- 추세 DOWN / SPY보다 약함 → 게이트 실패 → `enter=False`(사유 포함).
- 눌림목: 단조 상승(눌림 없음) → 트리거 미충족 → `enter=False`(눌림 대기).

## 비범위 (이 step에서 하지 않음)
- 체결(다음날 시가)·슬리피지·수수료 — 백테스트 엔진(step5)·executor.
- 사이징·스탑 — step2 sizing.
- 레짐 *분류* — step1(여기선 분류된 regime을 입력으로 받아 소비).
- 청산 — step4 exits. I/O — step6.

---

## step9 갱신 (B 레짐 고확신 게이트, 헌장 §8)

- **B(NERVOUS_BULL) 고확신**: regime==B면 더 빡센 게이트 — 기존 게이트(trend UP + 단기 상대강도 + allow) 위에
  **장기 상대강도 지속**까지 요구한다(`relative_strength(lookback=rs_lookback_long, 기본 126) is True`). A는 기존 게이트만.
  구조적 정의 = "trend UP(50d>200d 정렬) AND 상대강도 상위(단기+장기 지속)". ⚠️ 정확 임계(rs_lookback_long)는 편향 없는 데이터(B단계)서 튜닝.
- `pullback_entry`/`breakout_entry`에 `rs_lookback_long: int = 126` 파라미터 추가(공통 게이트에 전달).
