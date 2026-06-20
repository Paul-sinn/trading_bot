# SPEC: run_sim 청산(exit) 설정 배선

run_sim이 **기존 sim_exit 시스템**으로 청산 동작을 시험할 수 있게 한다. 기본 동작은 그대로 — 청산
플래그가 없으면 포지션은 OPEN으로 남는다. 새 청산 로직을 만들지 않고 `ExitParams`/`evaluate_exit`/
`apply_exit`를 재사용한다.

관련: `agents/sim_exit.py`(ExitParams, evaluate_exit, apply_exit), `agents/multiday.py`(일별 청산),
`agents/historical_sim.py`(구동), `scripts/run_sim.py`(CLI).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/이벤트 캘린더 미연결. 기본(플래그 없음) 동작 불변.

## 상위 청산 설정 (ExitPolicy, sim_exit.py)
포지션 무관 상위 설정을 포지션별 ExitParams로 변환한다(매일 동적):
- `stop_loss_pct`: 진입가 대비 손절(stop_price = avg_entry × (1 - pct)).
- `trail_pct`: 추적 고점 대비(포지션의 trailing_high 사용 — apply_exit가 처리).
- `max_hold_days`: 보유 일수(hold_days) 도달 시 시간청산.
- `manual_exit_date`: 그 날짜에 전량 청산.
- `is_active`: 하나라도 설정되면 True. 없으면 비활성.
- fail-closed: 범위 밖 비율(0<pct<1 아님)/0 이하 max_hold_days → ValueError.
- `exit_params_for_position(policy, *, avg_entry_price, hold_days, manual)`: 변환(순수).

## multiday 동적 청산
`run_phase1_multiday(..., exit_policy=None)`: exit_policy가 활성이면 매일 보유 포지션의 진입가/보유일로
ExitParams를 만들어 청산 평가(entry 전, 우선순위 manual>stop>trailing>time). 보유일은 루프가 추적
(청산되면 리셋). exit_policy 없으면 기존 `DayInput.exits`만 사용 — **기본 동작 불변**.

## historical_sim
`run_historical_simulation(..., exit_policy=None)`로 전달. exit_policy 활성 시 정적 default_exit_params는
무시(이중 청산 방지). 미설정이면 청산 없음(포지션 OPEN).

## run_sim CLI (선택 플래그)
- `--stop-loss-pct`, `--trailing-stop-pct`, `--max-holding-days`, `--manual-exit-date`.
- 아무 것도 안 주면 ExitPolicy=None → 결과 기존과 동일.
- 주면 historical_sim이 매일 청산 평가. 리포트(성과 + trade_diagnostics)가 exit_reason/실현 PnL/잔여
  OPEN/MDD를 보여준다(이미 구현 — 추가 변경 없음).
- 잘못된 값 → DataAdapterError → exit code 2(fail-closed).

## 테스트 (tests/test_exit_wiring.py) — fixture
- 청산 플래그 없으면 결과 불변(포지션 OPEN 유지).
- stop-loss가 손실 포지션을 청산(stop_loss_hit, 실현 PnL 음수).
- trailing-stop이 고점 이후 하락에서 청산(trailing_stop_hit).
- max-holding-days가 오래된 포지션을 청산(time_stop).
- vetoed 후보는 여전히 매매 0.
- real_orders_placed == 0.

## 비범위
- 라이브 청산/브로커, 전략·청산 규칙 튜닝, 부분청산 비율 CLI, 손익분기 트레일 등 고급 룰.
