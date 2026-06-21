# SPEC: signal_ablation (시그널 제거(ablation) 테스트 — 실험/리포트 전용)

무엇이 결과를 끌고 가는지 본다: 모멘텀·볼륨·추세/레짐·청산·심볼 집중. 잠긴 베이스라인을 그대로 두고
"하나씩 제거" 변형과 비교한다. 두 종류로 명확히 구분한다:

- **true-rerun**: 기존 run_sim 청산/심볼 플래그만 바꿔 실제 재시뮬(프로덕션 로직 미변경).
- **shadow-approx**: 모멘텀/볼륨/추세는 스캐너/디시전/RiskGate 안에 있어 진짜 제거하려면 프로덕션
  로직을 바꿔야 한다. **절대 바꾸지 않는다.** 대신 이미 실현된 트레이드를 진입 피처 기준으로 제거하는
  리포트 전용 근사. 리포트에 "shadow / approximate — 실현 트레이드 제거이지 진짜 전략 재시뮬 아님"으로 명시.

잠긴 베이스라인: entry_fill_model next-bar-limit, buffer 0.03, max_holding 60, stop 0.15, trailing 0.20,
fractional. winner extension 미적용, **갭 가드 미적용**, next-open 미사용. 기본 유니버스/주말청산 기본값 불변.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 기본 동작 불변.

## 변형
true-rerun(청산/심볼 플래그만):
- baseline / no_stop_loss / no_trailing_stop / no_time_stop / no_stop_no_trailing
- no_exit_controls(stop·trailing·time 모두 off — 안전, 포지션은 데이터 끝까지 보유 후 마크)
- no_MU / no_top3_symbols(심볼 제거)

shadow-approx(진입 피처로 실현 트레이드 제거 — 근사):
- shadow_drop_low_momentum(momentum_score 중앙값 미만 제거)
- shadow_drop_low_volume(volume_ratio_20d 중앙값 미만 제거)
- shadow_drop_non_uptrend(price_above_20ma=False 제거)

## 측정(변형별)
- cumulative return / total PnL / MDD / return/MDD / win rate / trade count.
- avg·median 트레이드 PnL, top1·top3 PnL share, best/worst 심볼, 분기 PnL.
- beats SPY? / beats QQQ?
- mode(true-rerun|shadow-approx), note.
- shadow는 MDD/return·MDD를 n/a로 둔다(equity 경로 미재현 — 과대 주장 금지).

## 출력 — AblationReport
- variants(AblationResult), warnings, real_orders==0.

## 함수
- `summarize(name, mode, legs, *, starting_cash, performance=None, spy, qqq, eq, note) -> AblationResult`.
- `shadow_drop(legs, snapshot_index, feature, *, is_flag=False) -> tuple[legs]` (약한 진입 피처 제거).
- `quarterly_pnl(legs) -> tuple`, `build_ablation(variants) -> AblationReport`, `format_ablation_markdown(report) -> str`.
- 러너 `experiments/signal_ablation_test.py` (`python -m experiments.signal_ablation_test`).

## 리포트가 답할 것 (정직, 과대 주장 금지)
- 어떤 컴포넌트가 가장 중요한가(제거 시 PnL 변화 최대).
- 청산이 가치를 더하나 해치나(no_exit_controls vs baseline).
- trailing이 stop loss보다 중요한가.
- 60일 time stop이 돕나(no_time_stop vs baseline).
- 결과가 MU/top3에 얼마나 의존하나.
- 어떤 변형이 true-rerun이고 어떤 게 shadow 근사인가.

## 테스트 (tests/test_signal_ablation.py)
- 베이스라인 파라미터·기본 유니버스·run_sim 기본값 불변, 레버리지 미혼합, 주말청산 빈 집합.
- 변형이 리포트 전용 경로 사용, 브로커/라이브 미사용, real_orders==0.
- no_MU/no_top3 심볼 정확히 제거, shadow 변형이 markdown에 명확히 표기.

## 비범위
- 스캐너/디시전/RiskGate 수정, 실 모멘텀/볼륨 재시뮬, 자본 재배분 변경, 라이브, 베이스라인/유니버스 변경.
