# SPEC: sizing (알고리즘 Layer 3 — 포지션 사이징)

알고리즘 3레이어 중 **Layer 3**. Layer 1(signals)·Layer 2(filters)를 모두 통과한 종목의
진입 수량과 스탑로스를 결정한다. Kelly Criterion 변형 · ATR 기반 스탑로스 · 투자성향 가중 ·
최대 리스크% 한도를 결합한다.

관련 문서: PRD(알고리즘 3레이어 — Layer 3 사이징: Kelly Criterion 변형, 스탑로스 = 진입가 −(ATR×배수),
투자성향 가중치, 최대 리스크% 한도), ADR-002(알고리즘은 부수효과 없는 순수 함수),
ADR-003(리스크 한도 — kill-switch / 한도 초과 차단), ADR-006(SDD→TDD).

CRITICAL: 이 모듈은 **부수효과 없는 순수 함수**다. 파일/네트워크/DB/전역상태 접근 금지.
입력만으로 출력(PositionPlan)이 결정된다.

CRITICAL (ADR-003): `position_size`의 최종 리스크액은 `account_equity * max_risk_pct`를
**절대 초과하지 않는다**. 어떤 입력 조합에서도 초과하면 수량을 줄여 한도 내로 맞춘다.
실거래에서 한도 초과 손실은 시스템의 가장 큰 위험이다.

CRITICAL: 풀 Kelly를 cap 없이 쓰지 않는다(과도한 변동성/파산 위험). `cap`(기본 0.25, half-Kelly 권장)으로 상한.

CRITICAL: `import talib` 금지. 분모 0(`win_loss_ratio=0`, `entry==stop`)을 반드시 안전 처리한다(ZeroDivision 금지).

## PositionPlan (결과)

```python
@dataclass(frozen=True)
class PositionPlan:
    quantity: int           # 진입 수량(현물 가정, 정수 floor). 0이면 "진입 안 함".
    stop_loss: float        # 스탑로스 가격(하한 0).
    risk_amount: float      # 최종 리스크액 = quantity * (entry - stop_loss). 한도 이하 보장.
    kelly_fraction: float   # 적용된 Kelly 분수(cap 적용 후).
```

## 함수

### `kelly_fraction(win_rate: float, win_loss_ratio: float, cap: float = 0.25) -> float`
- 기본 Kelly: `f = win_rate - (1 - win_rate) / win_loss_ratio`.
- 변형: `f < 0`이면 `0`(베팅 안 함). `f > cap`이면 `cap`으로 상한(half-Kelly 권장).
- `win_loss_ratio <= 0`(분모 0/음수): 베팅 근거 없음 → `0` 반환(ZeroDivision 금지).
- 경계: `win_rate=0` → 음수 → `0`. `win_rate=1` → `f=1` → cap 적용. 음수 `win_rate`는 음수 f → `0`.
- 반환값은 항상 `[0, cap]` 범위.

### `stop_loss_price(entry: float, atr: float, multiplier: float) -> float`
- `stop = entry - atr * multiplier`.
- 음수가 되지 않도록 하한 `0` (`max(0.0, stop)`). 가격은 음수일 수 없다.

### `risk_appetite_weight(appetite: float) -> float`
- `appetite`: `0.0`(보수적) ~ `1.0`(공격적).
- 선형 매핑으로 사이즈 가중치 반환: 보수적일수록 작게, 공격적일수록 크게.
- 범위 밖 입력은 `[0.0, 1.0]`로 clamp.
- 반환 범위는 `(0, 1]`로 두어(예: `0.5 + 0.5*appetite`) Kelly 분수와 곱해도 한도를 넘기지 않게 한다.
- 공격적(`appetite=1.0`)이 보수적(`appetite=0.0`)보다 큰 가중치를 반환한다.

### `position_size(account_equity, entry_price, stop_loss_price, max_risk_pct, kelly_f, appetite_weight) -> PositionPlan`
- 1주당 리스크 `per_share_risk = entry_price - stop_loss_price`.
- 허용 리스크액 `allowed_risk = account_equity * max_risk_pct`.
- 기본 수량 `base_qty = allowed_risk / per_share_risk` (per_share_risk>0 일 때).
- 사이징 반영: `qty = floor(base_qty * kelly_f * appetite_weight)` (현물 가정, 정수 floor).
- **CRITICAL 한도 상한(ADR-003)**: `qty * per_share_risk`가 `allowed_risk`를 초과하면
  `qty = floor(allowed_risk / per_share_risk)`로 줄인다. 최종 `risk_amount`는 `allowed_risk` 이하 보장.
- `risk_amount = qty * per_share_risk`.

## 엣지케이스
- `per_share_risk <= 0` (entry == stop, 또는 stop > entry): 계산 불가 → `quantity=0`, `risk_amount=0` (ZeroDivision 금지).
- `account_equity <= 0` 또는 `max_risk_pct <= 0`: 허용 리스크 0 → `quantity=0`.
- `kelly_f <= 0` 또는 `appetite_weight <= 0`: 베팅 안 함 → `quantity=0`.
- `base_qty < 1`(허용 리스크가 1주에도 못 미침): floor → `0` → "진입 안 함".
- 보수적(`appetite_weight` 작음) < 공격적(큼): 동일 조건에서 공격적 수량이 더 크거나 같다.

## 비범위 (이 step에서 하지 않음)
- Layer 1(signals, step 5), Layer 2(filters, step 6).
- 실제 주문 실행/체결/슬리피지(executor 에이전트), 리스크 에이전트 kill-switch 루프.
- I/O(데이터 fetch, DB, MCP). 입력값은 호출자가 준비한다.
