# SPEC: executor_agent (실행 에이전트 — 리스크 게이트 통과 후 주문 실행 + 체결/슬리피지 기록)

실행 에이전트는 판단 에이전트의 결과(BUY/SELL)를 받아 **리스크 게이트를 통과한 뒤에만**
주문을 실행하고, 체결을 확인하며 슬리피지를 기록한다. MCP 주문은 mock으로 추상화한다.

관련 문서: PRD(실행 = MCP 주문 실행, 체결 확인, 슬리피지 기록),
ARCHITECTURE(자동매매 루프: 판단 → [PreToolUse hook: 리스크 게이트] → 실행 에이전트(MCP)),
ADR-003(리스크 게이트 / kill-switch), ADR-001(외부 의존은 backend·agent에 격리),
`specs/agent_base.md`(Agent·AgentRegistry), `specs/risk_agent.md`(`check_risk_gate`/`RiskAgent`),
`specs/decision_agent.md`(`Decision`).

CRITICAL (CLAUDE.md / ADR-003): 모든 자동 주문은 반드시 **리스크 게이트**를 통과해야 한다.
어떤 코드 경로로도 게이트를 우회해 `place_order`에 도달할 수 없다. 게이트 차단·`registry.killed`·
게이트 예외 시 주문은 **반드시 거부(None)** 된다(**fail-closed**). 시스템 최대 위험은 한도 초과
실거래다.

CRITICAL: 실제 Robinhood 주문을 실행하지 않는다. 이 phase는 결정론적 `MockOrderProvider`만
사용한다. Robinhood는 공개 API 키가 없다 — robinhood-trading MCP 서버로 주문하며,
`RobinhoodOrderProvider`는 골격 + 명확한 예외까지만(실호출 금지, MCP 실연동은 통합 phase).

## 데이터 모델

```python
@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    limit_price: float | None = None


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    requested_price: float
    filled_price: float
    slippage: float        # filled_price - requested_price (방향 고려)
```

- `slippage = filled_price - requested_price`. 매수(buy)에서 양수면 불리(비싸게 체결),
  매도(sell)에서 음수면 불리. 부호는 가격 차이를 그대로 보존한다(방향 해석은 소비측).

## Provider 인터페이스 (외부 의존 주입)

### `OrderProvider`
```python
class OrderProvider(Protocol):
    async def place_order(self, req: OrderRequest) -> Fill: ...
```
- 실거래 provider(Robinhood MCP 연동)는 후속 phase. 이 step은 `MockOrderProvider`만 사용.

### `MockOrderProvider`
- **결정론적** 체결: 요청가(`limit_price`, 없으면 생성자 기본가) 근처 + **고정 슬리피지**로 체결.
  난수·외부 호출 없음. 실제 거래 없음.
- `Fill`을 생성해 반환하며 `slippage`를 정확히 계산한다.

### `RobinhoodOrderProvider`
- robinhood-trading MCP 호출 구조는 **주석으로만** 남긴다(골격). 선택적 `mcp_client` 주입. 실제 주문 금지.
- 호출 시 실주문하지 않고 `NotImplementedError`로 차단한다(MCP 실연동은 통합 phase).

## ExecutorAgent(Agent)

```python
class ExecutorAgent(Agent):
    def __init__(self, registry: AgentRegistry, provider: OrderProvider,
                 risk_gate: Callable[[], tuple[bool, str]], *,
                 name: str = "executor") -> None: ...
    async def execute(self, req: OrderRequest) -> Fill | None: ...
    async def tick(self) -> None: ...
```

- `Agent`(step 0) 라이프사이클을 그대로 상속(IDLE/RUNNING/STOPPED, kill 후 start 거부).
- 생성자에 `AgentRegistry`, `OrderProvider`, **리스크 게이트 함수**(`check_risk_gate` 부분적용
  또는 `RiskAgent.evaluate` 래핑 등 `() -> (allowed, reason)`)를 주입한다.
- `execute(req) -> Fill | None` — **CRITICAL 순서**:
  1. `quantity <= 0`이면 즉시 거부(None) + 사유 기록.
  2. `registry.is_killed()`이면 즉시 거부(None) + 사유 기록.
  3. 리스크 게이트 호출. 게이트가 **예외**를 던지면 **fail-closed**(거부, None) + 사유 기록.
  4. 게이트가 차단(allowed=False)이면 거부(None) + 사유 기록.
  5. **통과한 경우에만** `provider.place_order(req)` 호출.
  6. 반환된 `Fill`을 내부 `fills` 리스트에 기록(슬리피지 포함). 콜백이 있으면 호출.
- 게이트 통과 없이는 어떤 경로로도 `place_order`에 도달하지 못한다.
- `tick()` — 에이전트 루프 1회: 현재 step은 주문 소스 연결 전이므로 부수효과 없이 동작
  (no-op). 후속 step에서 판단 결과와 배선한다.

## 엣지케이스

- `quantity == 0` 또는 음수 → 거부(None), `place_order` 미호출.
- `registry.is_killed()` → 거부(None), `place_order` 미호출.
- 게이트 차단(`RISK_KILL_SWITCH=on` 또는 `evaluate`/게이트 함수가 False) → 거부(None),
  `place_order` 미호출.
- 게이트 함수가 **예외** → fail-closed 거부(None), `place_order` 미호출.
- 정상(게이트 통과 + quantity>0) → `Fill` 반환, 슬리피지 계산, `fills`에 기록.
- `RobinhoodOrderProvider` 골격 호출 → `NotImplementedError`(실주문 차단).

## 비범위 (이 step에서 하지 않음)

- 실제 Robinhood MCP 주문·인증(주입 Mock provider만 사용).
- 리스크 게이트 자체 구현(step 1 `check_risk_gate`/`RiskAgent` 재사용).
- 판단 결과와의 실제 배선(tick은 no-op 단위만 제공).
- 리포트/알림 발송(후속 reporter/notifier 에이전트가 `fills`를 소비).
