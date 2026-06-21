# SPEC: entry_fill_mode (현실적 진입 체결 모드 — opt-in)

run_sim/historical_sim가 **다음-바 진입 체결 규칙**을 선택적으로 쓰게 한다. 같은-바 close 즉시 체결의
lookahead 대신, 시그널 다음 거래 바로 한정매수/시장가 체결을 모델링해 현실적 성과를 본다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. **기본 동작 불변**
(model=current). 스캐너/디시전/사이징/RiskGate 변경 없음 — 후보 평가·veto·사이징은 그대로다. 새 동작은
**opt-in 전용**. LLM/뉴스/라이브 이벤트 API 미연결. 전략 시그널 튜닝 없음.

## CLI 플래그
- `--entry-fill-model current|next-bar-limit|next-open` (기본 current).
- `--entry-limit-buffer-pct` (기본 0.03, next-bar-limit에서 사용).

## 동작
- `current`: 기존 동작 — 후보 reference_price(시그널일 close)에 그대로 체결.
- `next-bar-limit`:
  - 시그널/참조일 = 후보일. 주문은 다음 거래 바에 제출.
  - limit = reference_price × (1 + buffer).
  - `next_open ≤ limit` → next_open 체결. 아니고 `next_low ≤ limit ≤ next_high` → limit 체결. 그 외 → 미체결.
  - 다음 바 결측 → 미체결(data_missing).
- `next-open`: 다음 바 있으면 next_open 체결(marketable/high-fill, 약한 가격 통제로 명시). 없으면 미체결.

## 구현(주입 지점) + 체결일 정렬
- `resolve_entry_fill(reference_price, next_bar, model, buffer) -> float | None`(순수): 체결가 또는 미체결(None).
- current: `_build_day`가 같은 바에 체결(기존 동작 불변).
- next-bar 모드(체결일 정렬): `_build_deferred_days`가 신호는 후보일에 잡되 **체결/포지션은 다음 거래 바**에
  일어나게 day를 구성한다 — DayInput[i]는 전일(i-1) 신호를 이날 바 가격으로 체결(scanner=전일 scanner,
  contexts=체결가로 재작성). 미체결이면 quantity=0으로 기존 RiskGate가 veto(주문/체결/포지션 없음, 우회 없음).
  - 포지션/트레이드 entry_date = **fill_date**(다음 바). exits/time-stop·mark-to-market는 fill_date 기준
    (체결 전 바에는 포지션이 없음). 마지막 신호는 처리되는 다음 바가 없어 체결되지 않는다.
- 스캐너/디시전/사이징/RiskGate/phase1_flow는 손대지 않는다. 기본값 current는 경로를 바꾸지 않는다.

## 리포트(run_sim)
- model≠current면 current와 비교(가능 시): trades/cum_return/MDD/win_rate/total_pnl + 함의 fill rate.
- real_orders_placed = 0.

## 테스트 (tests/test_entry_fill_mode.py)
- 기본(current) 동작 불변(trade_log 동일).
- next-bar-limit: open 체결 / limit 체결 / 갭업 미체결.
- next-open: next_open 체결.
- 미체결 진입은 시뮬 트레이드/체결/포지션을 만들지 않음.
- 다음 바 결측 안전(미체결).
- real_orders_placed == 0.

## 비범위
- 분/틱 체결, 부분체결, 다음날 추격, 정확한 자본 재배분, 비연속 거래일의 다음-바 보정,
  스캐너/디시전/사이징/시그널 변경.
