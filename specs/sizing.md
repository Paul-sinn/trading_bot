# SPEC: sizing (알고리즘 Layer 3 — 포지션 사이징)

알고리즘 3레이어 중 **Layer 3**. 진입 수량과 스탑로스를 결정한다. fractional Kelly · 콜드스타트
shrinkage · ATR 스탑 · 투자성향 가중 · **레짐 사이징 배수** · 최대 리스크% 하드캡을 결합한다.

관련 문서: `docs/STRATEGY.md` §6(목표·MDD governor), §7(사이징), §8(레짐 배수)(최상위 권위),
`tasks/kelly-fix-prompt.md`(상세 스펙), ADR-002(순수 함수), ADR-003(하드캡), ADR-006(SDD→TDD),
`algorithms/regime.py`(step1 — RegimePolicy.size_multiplier).

CRITICAL: **부수효과 없는 순수 함수.** I/O·네트워크·DB·전역상태·난수 금지. 입력만으로 출력 결정.

CRITICAL (ADR-003): `position_size`의 최종 risk_amount는 `account_equity × max_risk_pct`를 **절대
초과하지 않는다.** 어떤 경로(켈리·레짐배수·appetite)로도, 어떤 입력에서도 초과 시 수량을 줄여 한도 내로 맞춘다.
**레짐 배수(≤1.0)는 캡을 *올리지* 못한다 — 오직 축소만 한다.**

CRITICAL: 분모 0(`win_loss_ratio<=0`, `entry==stop`) 안전 처리(ZeroDivision 금지). talib 금지.

## MDD governor & 콜드스타트 (헌장 §6·§7)
- **MDD 상한이 사이징의 governor다.** 풀 켈리는 50%+ 낙폭 → 분수 켈리(`fraction`)로 강하게 축소.
- `fraction`은 추상값이 아니라 **"백테스트 MDD가 설계목표 ≤15%로 나오도록" 역튜닝**되는 값이다(백테스트 엔진과
  함께 캘리브레이션, step5~7). 코드는 파라미터로 노출하고 기본값을 보수적(0.5)으로 둔다.
- **콜드스타트**: 거래기록 0 → 경험적 켈리 신뢰 금지(과대 베팅=파산). `effective_kelly_fraction`이 표본
  크기로 `prior → 켈리` 점진 전환(shrinkage). 켈리 입력 출처 = **백테스트 엔진(1순위) → 실거래 로그(2순위)**.
  백테스트 미구현 현재는 콜드스타트(prior=0, 고정 비율) 경로만 활성.

## PositionPlan (결과)

```python
@dataclass(frozen=True)
class PositionPlan:
    quantity: int           # 진입 수량(현물, floor). 0이면 "진입 안 함".
    stop_loss: float        # 스탑로스 가격(하한 0).
    risk_amount: float      # quantity × (entry - stop). allowed_risk 이하 보장.
    kelly_fraction: float   # 적용된 분수(레짐배수 반영 가능).
```

## 함수

### `kelly_fraction(win_rate, win_loss_ratio, *, fraction=0.5, cap=0.25) -> float`
**fractional Kelly with a hard cap** (문제 1 라벨버그 수정).
- `f_full = win_rate - (1 - win_rate) / win_loss_ratio`.
- `f_used = clamp(fraction × max(0, f_full), 0, cap)`.
- ⚠️ `min(f, cap)`만으로는 fractional Kelly가 아니다 — `fraction`이 **모든** 베팅을 비례축소한다(작은 베팅도).
- `win_loss_ratio <= 0` → 0 (분모 안전). `f_full <= 0` → 0. 반환 항상 `[0, cap]`.
- `fraction=1.0`이면 cap-only(상한 클램프만) 동작.
- 예: full 0.40 → 0.20 / full 0.10 → 0.05 / full 0.04 → 0.02 (fraction 0.5).

### `effective_kelly_fraction(win_rate, win_loss_ratio, sample_size, *, fraction=0.5, cap=0.25, prior_fraction=0.0, shrinkage_k=30) -> float`
콜드스타트 shrinkage (문제 2).
- `w = sample_size / (sample_size + shrinkage_k)`.
- `f_eff = w × kelly_fraction(win_rate, win_loss_ratio, fraction=fraction, cap=cap) + (1 - w) × prior_fraction`.
- `sample_size <= 0` → `w=0` → `prior_fraction` (켈리 미사용, 호출부 고정비율에 위임).
- `sample_size → ∞` → `w → 1` → ≈ kelly. 표본↑ → 켈리 비중 단조 증가.
- 반환 `[0, cap]` (prior_fraction도 `[0, cap]` 가정).

### `regime_adjusted_fraction(kelly_f, regime) -> float`
레짐 사이징 배수를 켈리 위에 곱하는 **별도 레이어** (문제 3, 헌장 §8).
- `= max(0, kelly_f) × policy_for(regime).size_multiplier`.
- A NORMAL_BULL ×1.0 / B NERVOUS_BULL ×0.5 / C·D ×0.0 → **C/D는 진입 없음**.
- 켈리 함수 자체는 순수 유지 — 배수는 이 레이어에서만. 배수는 백테스트 튜닝 대상.

### `stop_loss_price(entry, atr, multiplier) -> float`
- `stop = entry - atr × multiplier`, 하한 0(`max(0.0, stop)`).

### `risk_appetite_weight(appetite) -> float`
- `appetite [0,1]` clamp → `0.5 + 0.5 × appetite` ∈ (0, 1]. 공격적 > 보수적.

### `position_size(account_equity, entry_price, stop_loss_price, max_risk_pct, kelly_f, appetite_weight) -> PositionPlan`
- `per_share_risk = entry - stop`, `allowed_risk = equity × max_risk_pct`.
- `qty = floor(allowed_risk / per_share_risk × kelly_f × appetite_weight)`.
- **CRITICAL 하드캡(ADR-003, 2중 안전)**: `qty > floor(allowed_risk/per_share_risk)`이면 그 값으로 클램프.
  최종 `risk_amount = qty × per_share_risk ≤ allowed_risk` 보장. (`kelly_f`에 레짐배수가 이미 반영됐어도 캡 유지.)

## 엣지케이스
- `per_share_risk <= 0`, `equity <= 0`, `max_risk_pct <= 0`, `kelly_f <= 0`, `appetite_weight <= 0` → quantity 0.
- `base_qty < 1` → floor 0 → 진입 안 함.
- 레짐 C/D → `regime_adjusted_fraction=0` → quantity 0.
- 보수적 weight < 공격적 weight → 공격적 수량 ≥ 보수적.

## 비범위 (이 step에서 하지 않음)
- 켈리 입력(win_rate/ratio/sample_size) 추정 = 백테스트 엔진(step5~7).
- 실제 주문 실행/체결/슬리피지(executor), RiskAgent kill-switch 루프.
- I/O. 입력값은 호출자가 준비한다.

---

## step10 갱신 (공격성 = MDD 예산 다 쓰기, 헌장 §6)

- ⚠️ **공격성 레버는 `max_risk_pct`(매매당 리스크 예산)**다. `position_size`가 risk를 `equity×max_risk_pct`로
  하드캡(ADR-003)하므로 `base_fraction`을 1.0 위로 올려도 효과 없음 — 실제로 예산을 키우려면 max_risk_pct를 올린다.
- 헌장 §6: MDD를 *덜* 쓰는 것도 실수(v1은 9.8%만 씀). max_risk_pct를 올려 백테스트 MDD가 설계 12~15%에 닿게 한다.
- **불변(ADR-003·20% 천장)**: 어떤 max_risk_pct에서도 매매당 risk_amount ≤ equity×max_risk_pct, 포트폴리오 MDD ≤ 20%.
  max_risk_pct↑ → MDD·총수익 단조 증가하되 20% 천장 안에서.
- ⚠️ **편향 데이터 과튜닝 금지**: 기본 max_risk_pct는 보수적 유지(0.01). 공격적 값은 calibrate 제안 + 재실행으로
  시연하되 **확정은 편향 없는 데이터(B단계)**. base_fraction은 콜드스타트 고정비율(1.0=예산 풀사용)로 유지.

---

## 헌법 account-risk 브리지 (policy enforcement 연결)

`position_size`의 출력(달러 단위 수량·`risk_amount`)을 헌법 두 리스크 불변식(`algorithms.policy.evaluate_risk`)이
소비하는 **분수 입력**으로 변환하는 순수 헬퍼. sizing이 포지션 수학의 단일 진실이므로 여기서 파생한다(policy는
평가만 — 중복 계산 안 함). 이 헬퍼들은 policy를 import하지 않는다(순수·무순환).

CRITICAL (fail-closed): `account_equity <= 0`·`entry_price <= 0`은 무효 계좌/입력 → `inf` 반환. `inf`는 이후
`evaluate_risk`의 캡 비교에서 자동으로 veto된다(0.0을 돌려 "안전해 보이게" 만들지 않는다). `agents/risk.py`의
`current_loss_ratio`가 `total_equity<=0`에 `inf`를 쓰는 것과 동일한 패턴.

### `per_trade_risk_pct(risk_amount, account_equity) -> float`
- `= risk_amount / account_equity` (분수). 불변식①(≤ SYSTEM_MAX_RISK_PCT 0.05) 입력.
- `account_equity <= 0` → `inf`(fail-closed).

### `position_weight(quantity, entry_price, account_equity) -> float`
- `= (quantity × entry_price) / account_equity` (분수). 포지션이 계좌에서 차지하는 비중.
- `account_equity <= 0` → `inf`(fail-closed).

### `stop_loss_pct(entry_price, stop_loss) -> float`
- `= (entry_price - stop_loss) / entry_price` (분수). 스탑 도달 시 포지션 손실률.
  `account_loss_pct = position_weight × stop_loss_pct` (불변식②) 입력.
- `entry_price <= 0` → `inf`(fail-closed). stop ≥ entry(무효 스탑)면 ≤ 0 → 이후 evaluate_risk가 veto.

### 비범위
- 이 헬퍼는 분수 변환만. 두 불변식 판정·veto는 `algorithms.policy.evaluate_risk`(단일 진실).
- position_weight 자체의 제안(Concentration Phase 캡)·실주문은 후속 step.
