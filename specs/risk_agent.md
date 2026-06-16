# SPEC: risk_agent (리스크 에이전트 — 실시간 리스크% 계산 + kill-switch)

리스크 에이전트는 포트폴리오 스냅샷을 주기적으로 평가해 **리스크 한도 초과 시 전 에이전트를
kill-switch로 강제 정지**한다. 안전(리스크 차단)이 최우선이며, 판단이 불확실하거나 예외가 나면
**fail-closed**(차단/kill)로 처리한다.

관련 문서: PRD(리스크 에이전트 = 실시간 리스크% 계산, 한도 초과 시 전 에이전트 kill-switch),
ADR-003(kill-switch는 PreToolUse hook으로 강제, 한도 초과 주문 원천 차단, 안전 최우선),
ADR-002(계산은 순수 함수 / I/O는 에이전트 루프), `specs/agent_base.md`(Agent·AgentRegistry).

CRITICAL: 기존 `check_risk_gate()`의 시그니처/동작(`RISK_KILL_SWITCH`)을 **제거·변경하지 않는다**.
PreToolUse hook(`.claude/hooks/pre_tool_use_risk.py`)과 기존 테스트가 의존한다.

CRITICAL: 외부(Robinhood/Claude) API를 직접 호출하지 않는다. 주입된 `PortfolioProvider`(Mock)만 쓴다.

## 데이터 모델

```python
@dataclass(frozen=True)
class RiskLimits:
    max_risk_pct: float       # 미실현 손실 비율 한도(%)
    max_drawdown_pct: float   # 당일 드로우다운 한도(%)
    max_position_pct: float   # 단일 포지션 노출 한도(% of total_equity)
```

`Portfolio`/`Position`은 `backend/app/services/portfolio.py`를 재사용한다
(`total_equity`, `cash`, `positions[symbol/quantity/avg_buy_price/current_price]`, `day_pnl`).

## 순수 계산 함수 (부수효과 없음 — ADR-002)

### `unrealized_loss(portfolio) -> float`
손실 중인 포지션의 미실현 손실 합(양수, 달러).
포지션별 기여 = `max(0, (avg_buy_price - current_price) * quantity)`. 이익 포지션은 0 기여.

### `current_risk_pct(portfolio: Portfolio, limits: RiskLimits) -> float`
계좌 대비 현재 떠안고 있는 리스크 비율(%).
- 공식: `unrealized_loss(portfolio) / total_equity * 100`.
- `total_equity <= 0`이면 분모가 무효 → `float("inf")` 반환(ZeroDivision 없이 안전,
  evaluate에서 자연히 차단으로 이어짐). `limits`는 시그니처 호환을 위해 받되 계산에는 쓰지 않는다
  (리스크%는 한도와 무관한 객관 측정치).

### `drawdown_pct(portfolio) -> float`
당일 피크 대비 하락률(%).
- 시작 자산 = `total_equity - day_pnl` (당일 손익을 되돌린 값 = 당일 시작 자산).
- `day_pnl >= 0`(손실 없음)이면 `0.0`.
- 그 외 `(-day_pnl) / start_equity * 100`. `start_equity <= 0`이면 `float("inf")`.

### `max_position_pct_used(portfolio) -> float`
가장 큰 단일 포지션의 시장가치(`quantity * current_price`)가 `total_equity`에서 차지하는 비율(%).
- 포지션 없음 → `0.0`. `total_equity <= 0` → `float("inf")`.

## RiskAgent(Agent)

```python
class RiskAgent(Agent):
    def __init__(self, registry: AgentRegistry, provider: PortfolioProvider,
                 limits: RiskLimits, name: str = "risk") -> None: ...
    def evaluate(self, portfolio: Portfolio) -> tuple[bool, str]: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속(IDLE/RUNNING/STOPPED, kill 후 start 거부).
- `evaluate(portfolio) -> (within_limits, reason)` — **순수 판정 함수**(I/O 없음, 테스트 용이).
  순서대로 검사하고 **첫 위반에서 차단**한다:
  1. `total_equity <= 0` → `(False, "total_equity <= 0 — 무효 계좌 상태")`.
  2. `current_risk_pct > max_risk_pct` → `(False, "미실현 손실 …% > 한도 …%")`.
  3. `drawdown_pct > max_drawdown_pct` → `(False, "드로우다운 …% > 한도 …%")`.
  4. `max_position_pct_used > max_position_pct` → `(False, "포지션 노출 …% > 한도 …%")`.
  5. 모두 통과 → `(True, "리스크 한도 내")`.
  - 경계값(정확히 한도와 같음)은 **허용**한다(`>`만 위반).
- `tick()` — 에이전트 루프 1회:
  1. `provider.get_portfolio()` 호출. **예외가 나면 fail-closed**:
     `registry.kill_all(...)` 호출 + `self.status = ERROR` 후 반환.
  2. `evaluate(portfolio)`로 판정. 한도 초과(`within=False`)면 `registry.kill_all(reason)`.
  3. kill_all은 멱등이므로 중복 tick이어도 안전(최초 사유 유지 — agent_base 보장).

## check_risk_gate (기존 보존 + registry 반영)

```python
def check_risk_gate(registry: AgentRegistry | None = None) -> tuple[bool, str]: ...
```

- 기존 동작 보존: `RISK_KILL_SWITCH == "on"` → `(False, …)`, 아니면 통과(무인자 호출 호환).
- 추가: `registry`가 주입되고 `registry.is_killed()`이면 `(False, kill_reason)`로 차단.
- fail-closed 원칙: 둘 중 하나라도 차단 신호면 차단. 우선순위는 env(`on`) → registry.

## 엣지케이스

- `total_equity == 0` → 계산 함수는 `inf` 반환(ZeroDivision 없음), `evaluate`는 차단.
- 빈 포지션 리스트 → `unrealized_loss=0`, `max_position_pct_used=0`. 다른 한도만 평가.
- `day_pnl < 0`(당일 손실) → `drawdown_pct` 양수. `day_pnl >= 0` → `0.0`.
- 한도 경계값(측정치 == 한도) → 허용.
- `provider.get_portfolio()`가 예외 → `tick()`은 `kill_all`(fail-closed) + status ERROR.
- `kill_all` 중복 호출(연속 위반 tick) → 멱등, 최초 사유 유지.

## 비범위 (이 step에서 하지 않음)

- 실제 Robinhood MCP/Claude 호출, 인증(주입 Mock provider만 사용).
- 주기 스케줄링/asyncio 루프 구동(tick 1회 단위만 제공).
- 알림 발송(notifier 에이전트, 후속 step).
