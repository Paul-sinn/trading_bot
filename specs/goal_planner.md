# SPEC: goal_planner (목표 기반 세팅 역산 — 순수 함수)

"목표금액 + 목표기간(개월)"으로부터 그 목표 달성에 필요한 **리스크·투자성향 세팅을 역산**하는
순수 함수 모음. 결정론적 계산만 한다(AI 결합은 step 1, 외부 I/O 금지).

관련 문서: PRD(목표 & 리스크, 투자성향, Layer 3 사이징), ADR-002(알고리즘은 부수효과 없는 순수 함수),
ADR-003(리스크 한도 — 시스템 하드캡 / 한도 초과 차단, **최우선**), ADR-006(SDD→TDD).
재사용: `algorithms/sizing.py`(`appetite`, `max_risk_pct`, `stop_loss_atr_multiplier`가 여기로 흘러간다),
`agents/risk.py`(`RiskLimits` — 역산 결과가 이 형태와 호환돼야 한다, **중복 정의 금지**).

CRITICAL: 이 모듈은 **부수효과 없는 순수 함수**다. 파일/네트워크/DB/Claude/전역상태 접근 금지. `import talib` 금지.

CRITICAL (ADR-003): 어떤 모드·어떤 목표에서도 `risk_limits.max_risk_pct`는 시스템 하드캡
`SYSTEM_MAX_RISK_PCT`(상수 = 0.05)를 **절대 초과하지 못한다**. 비현실적 목표(예: 1개월 10배)라고 해서
하드캡을 넘기지 마라 — 실거래 계좌 파산 위험. `SAFE` 모드는 더 낮은 캡(`SAFE_MAX_RISK_PCT` = 0.02)을 적용한다.

> 단위 주의: 이 모듈의 `max_risk_pct`/`max_drawdown_pct`/`max_position_pct`는 **분수**(fraction)다
> (0.05 = 5%). `sizing.position_size(max_risk_pct=...)`가 분수로 소비하는 것과 일치한다.

## 상수

```python
SYSTEM_MAX_RISK_PCT = 0.05   # 시스템 하드캡(ADR-003). 어떤 경우에도 max_risk_pct는 이 값을 넘지 못한다.
SAFE_MAX_RISK_PCT   = 0.02   # SAFE 모드 상한(더 보수적).
```

## enum / 데이터 모델

```python
class Feasibility(str, Enum):
    REALISTIC = "realistic"
    AMBITIOUS = "ambitious"
    UNREALISTIC = "unrealistic"

class PlanMode(str, Enum):
    SAFE = "safe"
    AGGRESSIVE = "aggressive"

@dataclass(frozen=True)
class GoalDerivedSettings:
    appetite: float                  # 투자성향 0.0(보수적)~1.0(공격적)
    risk_limits: RiskLimits          # agents.risk.RiskLimits 재사용
    stop_loss_atr_multiplier: float  # ATR 배수 (높을수록 넓은 스탑)
    feasibility: Feasibility
    required_monthly_return: float   # 역산된 필요 월 수익률(분수)
```

## 함수

### `required_monthly_return(current_equity: float, target_amount: float, months: int) -> float`
- 복리 기준 월 수익률: `(target_amount / current_equity) ** (1 / months) - 1`.
- `current_equity <= 0` → `ValueError`(분모/복리 무효).
- `months <= 0` → `ValueError`(기간 무효).
- `target_amount <= current_equity`(이미 달성) → 공식이 자연히 `<= 0` 반환(예외 아님).
  - `target == current` → `0.0`, `target < current` → 음수.
- 경계 예: `10000 → 12000`, `6개월` → 약 `0.0309`(≈3.1%/월).

### `feasibility(monthly_return: float) -> Feasibility`
임계값(보수적으로):
- `monthly_return <= 0.03` → `REALISTIC`.
- `monthly_return <= 0.08` → `AMBITIOUS`.
- 초과 → `UNREALISTIC`.
- 경계: `0.03` → REALISTIC, `0.08` → AMBITIOUS. 0 이하도 REALISTIC.

### `derive_settings(current_equity, target_amount, months, mode: PlanMode) -> GoalDerivedSettings`
1. `r = required_monthly_return(...)` (입력 무효 시 ValueError 전파).
2. `feas = feasibility(r)`.
3. 강도(intensity) `t = clamp(r / 0.08, 0.0, 1.0)` — 필요 수익률이 높을수록 1에 가깝다(`r<=0` → 0).
4. 모드별 캡으로 선형 매핑(`lerp(lo, hi, t)`):
   - `appetite = lerp(0.0, appetite_cap, t)` — SAFE `appetite_cap=0.5`, AGGRESSIVE `1.0`.
   - `max_risk_pct = lerp(0.005, risk_cap, t)` — SAFE `risk_cap=SAFE_MAX_RISK_PCT(0.02)`,
     AGGRESSIVE `risk_cap=SYSTEM_MAX_RISK_PCT(0.05)`.
   - `max_drawdown_pct = lerp(0.05, dd_cap, t)` — SAFE `0.10`, AGGRESSIVE `0.20`.
   - `max_position_pct = lerp(0.10, pos_cap, t)` — SAFE `0.20`, AGGRESSIVE `0.40`.
   - `stop_loss_atr_multiplier = lerp(1.5, stop_cap, t)` — SAFE `2.5`, AGGRESSIVE `3.0`(높은 필요수익률 → 넓은 스탑).
5. **CRITICAL 하드캡(ADR-003)**: 매핑 후에도 `max_risk_pct = min(max_risk_pct, mode_cap, SYSTEM_MAX_RISK_PCT)`로
   한번 더 clamp. 어떤 경로로도 `SYSTEM_MAX_RISK_PCT` 초과 불가.
6. 모든 값은 정의된 범위로 clamp(`appetite` ∈ [0,1] 등).

불변식:
- 같은 목표에서 `AGGRESSIVE`의 `max_risk_pct`·`appetite`는 `SAFE` 이상(둘 다 하드캡 이하).
- 어떤 입력에서도 `risk_limits.max_risk_pct <= SYSTEM_MAX_RISK_PCT`.
- 보수적 목표(긴 기간·작은 증가 → 작은 `r`)는 낮은 `appetite`/`max_risk_pct`.

## 엣지케이스
- `current_equity <= 0` / `months <= 0` → `ValueError`(`required_monthly_return`에서).
- 이미 달성(`target <= current`) → `r <= 0` → `t=0` → 최소 세팅, `feasibility=REALISTIC`.
- 극단적 비현실 목표(예: 1개월 10배) → `t=1` → 각 모드 상한이지만 `max_risk_pct <= SYSTEM_MAX_RISK_PCT`.

## 비범위 (이 step에서 하지 않음)
- AI(Claude) 결합·서비스화(step 1), API(step 2), 프론트 페이지(step 3).
- 외부 I/O(파일/네트워크/DB/MCP). 입력값은 호출자가 준비한다.
- `RiskLimits` 재정의(agents.risk 재사용).
