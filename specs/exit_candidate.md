# SPEC: exit_candidate (후보 청산 정책 검증 — 실험/리포트 전용)

청산 딥다이브에서 hold_45_trailoff(stop 15 / trail off / hold 45)가 현재 데이터 best 후보로 나왔다.
이 후보가 미래 베이스라인 후보가 될 만큼 강건한지, 잠긴 베이스라인을 바꾸지 않고 검증한다. 개선이
ARM 쏠림/단일 강세 구간 산물인지 본다. 모두 true-rerun — run_sim 청산 플래그만 바꾼다.

잠긴 베이스라인(변경 금지): 진입 next-bar-limit 3%, stop 0.15, trailing 0.20, hold 60, fractional.
winner extension·갭 가드 미적용, next-open 미사용, 주말청산 빈 집합. 진입/유니버스/스캐너/디시전/사이징/
RiskGate 미변경. **이 단계에서 베이스라인 승격 없음.**

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 기본 동작 불변.

## 정책
- locked_baseline: stop 15 / trail 20 / hold 60.
- candidate: stop 15 / trail off / hold 45 (hold_45_trailoff).
- alt_candidate(선택): stop 20 / trail off / hold 60.

## 정책별 검증
- 풀기간 결과(return/PnL/MDD/ret·MDD/win/trades/avg·median PnL/avg hold/top1·top3/best·worst).
- 분기·연도 PnL, positive active quarters.
- leave-one-symbol-out(rerun) + worst-drop.
- no_MU / no_ARM / no_top3 (심볼 제거 rerun) → full 대비 delta.
- 슬리피지 0.5% / 1.0% (adj_pnl = pnl − entry×slip×qty, 단일 정책, 리포트 전용).
- SPY / QQQ / equal-weight 비교.
- 청산 사유 count / PnL.

## 출력 — CandidateValidationReport
- policies(PolicyValidation), baseline_name/candidate_name, warnings, real_orders==0.
- PolicyValidation: full(ExitVariantResult 재사용), eq_return, yearly, positive_quarters/active_quarters,
  slippage, loo, worst_drop, no_mu/no_arm/no_top3(DropResult).

## 함수
- `yearly_pnl(legs) -> tuple`, `positive_active_quarters(quarterly) -> (pos, active)`.
- `make_drop(name, full_pnl, drop_pnl, drop_return) -> DropResult`.
- `build_candidate_validation(policies, *, baseline_name, candidate_name) -> CandidateValidationReport`.
- `format_candidate_validation_markdown(report) -> str`.
- 러너 `experiments/exit_candidate_validation.py` (`python -m experiments.exit_candidate_validation`).

## 리포트가 답할 것 (정직, 과대 주장 금지)
- 슬리피지 후 후보가 baseline을 이기나.
- return/MDD를 개선하나, raw PnL만인가.
- 개선이 대부분 ARM인가.
- no_MU / no_ARM / no_top3에서 살아남나.
- 분기 전반에서 강한가.
- baseline보다 안전한가 위험한가 그냥 더 공격적인가.
- 지금 baseline을 잠근 채 둬야 하나(→ 예).
- 승격 전 정확히 어떤 증거가 더 필요한가.

## 테스트 (tests/test_exit_candidate.py)
- 베이스라인 파라미터·기본 유니버스·run_sim 기본값 불변, 후보 파라미터 격리(기본 미변형).
- 슬리피지 변형 리포트 전용, LOO 심볼 정확 제거, 리포트가 "아직 승격 없음" 명시.
- 브로커/라이브 미사용, real_orders==0.

## 비범위
- 베이스라인 잠금 변경/승격, 진입/유니버스/스캐너/디시전/RiskGate 변경, winner extension/gap guard, next-open, 라이브.
