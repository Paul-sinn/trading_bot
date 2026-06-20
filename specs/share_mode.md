# SPEC: 분수주(fractional share) 시뮬 모드

$1,000 같은 소액 계좌가 고가주(예: $500 NVDA)를 **분수주**로 시뮬 매수할 수 있게 한다. 기존 정수주
(whole-share) 동작은 **기본값 그대로** 유지하고, 분수주는 **명시적 시뮬 옵션**으로만 켠다.

배경: 정수주 사이징은 `int(floor(...))`라 $1,000 계좌가 고가주에서 0주로 떨어져 `position_size 0`
hard-veto를 맞는다(헌장 fail-closed 정상 동작). Robinhood는 분수주를 지원하므로 소액 시뮬을 현실화한다.

CRITICAL: 실브로커/Robinhood API/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/이벤트 캘린더 미연결. **리스크 캡은 정확히 동일하게 적용**된다 — RiskGate는 규칙 위반 시 그대로
veto하고, veto된 후보는 시뮬 주문/체결을 만들지 않는다.

## 모드
- `ShareMode.WHOLE`(기본): 기존 정수주. `position_size`는 변경 없이 `int` 수량을 돌려준다.
- `ShareMode.FRACTIONAL`: 의도 notional을 분수주로 변환. 수량은 `lot_size`(예: 0.001 = 브로커형
  최소단위, config가 정하면 그 값) 배수로 내림한 `float`.

## 사이징 (algorithms/sizing.py — 순수 유지)
- `position_size(..., share_mode=ShareMode.WHOLE, lot_size=0.001)`:
  - WHOLE: 기존 경로 그대로(`int` 수량). 회귀 없음.
  - FRACTIONAL: `raw = base_qty * kelly_f * appetite_weight`; `qty = floor(raw/lot_size)*lot_size`.
    ADR-003 캡도 분수 단위로 동일 적용(`risk_amount = qty * per_share_risk <= account_equity*max_risk_pct`).
  - **fail-closed**: `lot_size <= 0` 또는 알 수 없는 share_mode → `ValueError`(호출부가 무효 사이징으로
    처리 → veto).
- `PositionPlan.quantity` 타입은 `float`로 확장(WHOLE 경로는 여전히 `int` 값을 저장 — 기존 테스트 유지).

## 배선
- `EvidenceParams`에 `share_mode: ShareMode = ShareMode.WHOLE`, `lot_size: float = 0.001` 추가 →
  `position_size`에 전달. 기본은 WHOLE이라 기존 동작 불변.
- `CandidateContext.quantity` / `SimulatedOrder.quantity` / `submit(quantity=...)` 타입을 `float`로 확장
  (정수주는 정수값 float). 검증 `quantity > 0`는 그대로. veto면 주문/체결 없음(우회 불가).
- `scripts/run_sim.py`에 `--share-mode whole|fractional`(기본 whole), `--lot-size`(기본 0.001) →
  `EvidenceParams`에 주입.

## 테스트 (tests/test_share_mode.py)
- WHOLE 기본 동작 불변(기본 == 명시 WHOLE, 수량 int, 소액·고가주는 0).
- $1,000 계좌 + 고가주 → FRACTIONAL에서 0보다 큰 분수 수량.
- 분수 수량이 notional·리스크 캡 준수(risk_amount ≤ allowed_risk, per_trade ≤ max_risk_pct).
- 분수 수량은 lot_size 배수(정밀도).
- 미설정/오설정(lot_size≤0, 잘못된 모드) → fail-closed(예외).
- veto된 후보는 분수 수량이어도 시뮬 주문/체결 0건.
- real_orders_placed == 0.

## 비범위
- 실 Robinhood 분수주 주문, 라이브 체결, 전략/시그널 변경, 분수주용 별도 슬리피지 모델.
