# Step 7: algo-sizing (Layer 3)

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (알고리즘 Layer 3 사이징 정의)
- `/docs/ADR.md` (ADR-002: 순수 함수 / ADR-003: 리스크 한도)
- `/algorithms/signals.py`, `/algorithms/filters.py`, `/specs/signals.md`, `/specs/filters.md` (step 5·6 산출물)

step 5·6의 순수 함수 패턴을 그대로 따르라.

## 작업

알고리즘 **Layer 3 (포지션 사이징)**을 순수 함수로 구현한다. Layer 1·2를 통과한 종목의 수량/스탑로스를 결정한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/sizing.md`

입력/출력/엣지케이스:
- **Kelly Criterion 변형**: `kelly_fraction(win_rate: float, win_loss_ratio: float, cap=0.25) -> float`
  - 기본 Kelly: `f = win_rate - (1 - win_rate) / win_loss_ratio`.
  - 변형: 음수면 0(베팅 안 함), `cap`으로 상한(풀켈리 위험 방지, half-Kelly 권장).
  - 엣지케이스: win_loss_ratio 0(분모 0), win_rate 0/1 경계, 음수 입력.
- **스탑로스**: `stop_loss_price(entry: float, atr: float, multiplier: float) -> float` = `entry - atr * multiplier`. 음수가 되지 않도록 하한 0.
- **투자성향 가중**: `risk_appetite_weight(appetite: float) -> float` — `appetite`는 0.0(보수적)~1.0(공격적). 사이즈/스탑 배수에 곱해질 가중치 반환.
- **최종 수량**: `position_size(account_equity, entry_price, stop_loss_price, max_risk_pct, kelly_f, appetite_weight) -> PositionPlan`
  - 리스크 기반: 1주당 리스크 = `entry - stop_loss`. 허용 리스크액 = `account_equity * max_risk_pct`. 수량 = 허용리스크액 / 1주당리스크, 그 위에 kelly_f·appetite_weight 반영.
  - CRITICAL: 최종 리스크액이 `account_equity * max_risk_pct`를 **절대 초과하지 않도록** 상한을 건다 (ADR-003 리스크 한도). 초과하면 수량을 줄인다.
  - 수량은 음수/소수주 정책에 맞게 정수 floor(현물 가정). 0이면 "진입 안 함".
- `PositionPlan(quantity: int, stop_loss: float, risk_amount: float, kelly_fraction: float)`.

### Step B. TEST (Red) — `tests/test_sizing.py`

- Kelly: 알려진 입력의 기댓값(예: win_rate=0.6, ratio=2 → f=0.4 → cap 0.25 적용). 음수→0. 분모 0 안전.
- 스탑로스 계산값, 음수 방지.
- `position_size`가 **max_risk_pct 한도를 절대 넘지 않는지** (여러 파라미터 조합으로 검증) — 가장 중요한 테스트.
- 보수적(appetite=0) vs 공격적(appetite=1)일 때 수량 증가 방향 검증.
- equity 부족/엣지케이스에서 수량 0.

### Step C. 구현 (Green) — `algorithms/sizing.py`

- 순수 함수. **`import talib` 금지.**
- 리스크 한도 상한 로직을 명시적으로 구현하고 주석으로 표시.

### Step D. 리팩터

Kelly·스탑·수량 계산을 작은 함수로 분리. 테스트 유지.

## Acceptance Criteria

```bash
pytest tests/test_sizing.py -v
python -c "from algorithms.sizing import kelly_fraction; print(kelly_fraction(0.6, 2.0))"
python -c "from algorithms.sizing import position_size; p=position_size(10000, 100, 95, 0.02, 0.25, 1.0); print(p); assert p.risk_amount <= 10000*0.02 + 1e-6"
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. 특히 마지막 assert(리스크 한도 미초과)가 통과해야 한다.
2. 아키텍처 체크리스트:
   - `position_size`가 `max_risk_pct` 한도를 어떤 입력에서도 초과하지 않는가? — ADR-003 (가장 중요)
   - 순수 함수인가? `talib` 미사용인가?
   - step 5·6 스타일과 일관적인가?
3. `phases/0-foundation/index.json`의 step 7을 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`

## 금지사항

- 리스크 한도(`account_equity * max_risk_pct`) 초과 수량을 허용하지 마라. 이유: 실거래에서 한도 초과 손실 — 시스템의 가장 큰 위험 (ADR-003).
- 풀 Kelly를 cap 없이 사용하지 마라. 이유: 과도한 변동성/파산 위험. cap(기본 0.25) 적용.
- 분모 0(win_loss_ratio=0, entry==stop)을 처리하지 않으면 안 된다. 이유: ZeroDivision.
- `import talib` 하지 마라. SPEC/TEST 없이 구현부터 하지 마라(ADR-006).
- 기존 테스트(step 5·6)를 깨뜨리지 마라.
