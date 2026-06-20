# SPEC: multiday (다일 시뮬레이션 루프)

Phase 1 dry-run 흐름을 여러 거래일에 걸쳐 돌리며 **같은 시뮬 포트폴리오를 다음 날로 이월**한다. 일별로
scanner → evidence(컨텍스트) → RiskGate → 시뮬 주문 → 시뮬 체결 → 포트폴리오 갱신 → 리포트 스냅샷.

관련: `agents/phase1_flow.py`(run_phase1_dry_run, Phase1Result), `agents/sim_portfolio.py`
(SimulatedPortfolio, PortfolioSnapshot).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/이벤트 캘린더
실연동 없음. 전략 시그널 변경 없음.

CRITICAL: 포트폴리오 상태는 날을 넘겨 유지된다 — 기존 포지션·현금·노출·실현PnL·매매로그가 다음 날로
이월된다. 일별 흐름은 `run_phase1_dry_run`을 **동일 portfolio로 재사용**한다(중복 로직 없음). veto된
후보는 포트폴리오에 영향이 없고, 불가능 주문(현금/한도)은 그대로 차단된다.

## 데이터 모델 (frozen)
```python
@dataclass(frozen=True)
class DayInput:
    date: str
    scanner: object                 # async .scan() → list[Candidate]
    contexts: dict[str, CandidateContext]
    regime_name: str = "NORMAL_BULL"
    compass_state: str = "strong"
    account_phase: str = "1"
    risk_mode_name: str = "B"
    decision_provider: object | None = None   # 기본 MockDecisionProvider

@dataclass(frozen=True)
class MultiDayResult:
    day_results: tuple[Phase1Result, ...]      # 일별 결과(그날 주문/체결 + 누적 스냅샷)
    portfolio: SimulatedPortfolio              # 최종 누적 포트폴리오
    @property real_orders_placed -> 0
    @property daily_snapshots -> tuple[PortfolioSnapshot, ...]   # 일별 스냅샷(누적)
    @property trade_log -> tuple[TradeRecord, ...]               # 누적 매매로그
```

## 함수
### `async run_phase1_multiday(*, days, policy, account_cash=None, portfolio=None) -> MultiDayResult`
1. portfolio 없고 account_cash 있으면 `SimulatedPortfolio(account_cash)` 1개 생성(전 기간 공유).
2. 각 DayInput에 대해 `run_phase1_dry_run(..., portfolio=portfolio)` 호출 — 동일 portfolio가 이월된다.
   일별 executor는 새로 생기지만(그날 주문/체결 집계) portfolio는 공유.
3. 일별 Phase1Result 수집. 최종 portfolio + 일별 스냅샷 반환.

불변: 일별·최종 real_orders_placed=0. 포지션/현금/로그는 누적.

## 엣지케이스
- Day1 체결 후 Day2는 줄어든 현금/기존 포지션을 본다(공유 portfolio).
- Day2 후보 현금 부족 → 차단(포트폴리오 가드). veto된 Day2 후보 → 상태 불변.
- 빈 days → 빈 결과 + 초기 포트폴리오.

## 비범위
- 일별 가격 마킹/일일 미실현 PnL 평가, 청산 자동화, phase 자동 전환(equity↑), 실브로커/LLM/이벤트 캘린더,
  전략/시그널 변경.
