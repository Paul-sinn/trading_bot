# SPEC: exit_walk_forward (후보 청산 정책 워크포워드 검증 — 실험/리포트 전용)

후보 청산 정책(hold_45_trailoff)이 잠긴 베이스라인보다 워크포워드/롤링 윈도우에서 더 잘 버티는지
검증한다. 후보가 단일 분기/심볼에 쏠린 게 아닌지, 강세장 밖에서도 우위가 유지되는지 본다. 모두
true-rerun — run_sim 청산 플래그(stop/trail/max_hold)만 윈도우별로 바꾼다. 진입/유니버스/스캐너/
디시전/사이징/RiskGate 미변경. **이 단계에서 베이스라인 승격 없음.**

잠긴 베이스라인(변경 금지): 진입 next-bar-limit 3%, stop 0.15, trailing 0.20, hold 60, fractional.
winner extension·갭 가드 미적용, next-open 미사용, 주말청산 빈 집합.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 기본 동작 불변.

## 정책 (2~3)
- locked_baseline: stop 15 / trail 20 / hold 60.
- candidate: stop 15 / trail off / hold 45.
- alt_candidate(선택): stop 20 / trail off / hold 60.

## 윈도우 (가용 데이터 범위)
- yearly, quarterly, rolling 3m, rolling 6m, rolling 12m (rolling step 3m). 각 윈도우는 start_date만 바꿔
  독립 재시뮬(지표는 이전 히스토리까지 사용).

## out-of-bull 마킹 (정직 — 가짜 결론 금지)
- 매매가 일어난 윈도우가 모두 2025-2026이면 `OUT_OF_BULL_VALIDATION = NOT_AVAILABLE`,
  reason: insufficient local data history. 강세장 밖 우위는 데이터 확보 전 판정 불가.

## 윈도우×정책 측정
- return / PnL / MDD / return·MDD / win / trades / avg·median PnL / top1·top3 / best·worst.
- beats SPY? / beats QQQ? / beats equal-weight?

## 안정성 판정
- 후보가 baseline을 PnL로 이긴 윈도우 수 / return·MDD로 이긴 수 / MDD가 더 나쁜 수 / 음수 윈도우 수.
- worst/best 후보 윈도우. 후보 우위가 일관인지 한 분기/심볼에 집중인지(최대 단일 윈도우 우위 비중).

## 출력 — ExitWalkForwardReport
- policies, windows(정책별 WindowResult), compares(후보 vs baseline), verdict, out_of_bull, warnings, real_orders==0.

## 함수
- `generate_exit_windows(data_min, data_max) -> tuple[Window]` (year/quarter/roll3/roll6/roll12).
- `compute_window_compares(base_windows, cand_windows) -> tuple`.
- `compute_stability_verdict(compares, *, bull_years) -> StabilityVerdict`.
- `build_exit_walk_forward(...)`, `format_exit_walk_forward_markdown(report) -> str`.
- 러너 `experiments/exit_candidate_walk_forward.py` (`python -m experiments.exit_candidate_walk_forward`).

## 리포트가 답할 것 (정직)
- 후보가 윈도우 전반에서 일관되게 baseline을 이기나.
- 한 분기 때문에만 이기나.
- drawdown을 너무 자주 키우나.
- ARM/top3 집중 리스크가 남아 있나.
- 승격할 증거가 충분한가(→ 아직 아님).
- 잠긴 베이스라인을 그대로 둬야 하나(→ 예).

## 테스트 (tests/test_exit_walk_forward.py)
- 베이스라인 파라미터·기본 유니버스·run_sim 기본값 불변, 후보 파라미터 격리.
- 윈도우 생성(개수/경계), out-of-bull 미가용 마킹, 안정성 카운트.
- 베이스라인 승격 없음 명시, 브로커/라이브 미사용, real_orders==0.

## 비범위
- 베이스라인 승격, 진입/유니버스/스캐너/디시전/RiskGate 변경, winner extension/gap guard, next-open, 라이브, 신규 데이터 수집.
