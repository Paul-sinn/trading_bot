# SPEC: exit_deep_dive (트레일링 스톱/청산 정책 딥다이브 — 실험/리포트 전용)

시그널 ablation에서 트레일링 스톱이 현재 데이터에서 역효과로 보였다. 20% 트레일링이 실제로 전략을
해치는지, 잠긴 베이스라인을 바꾸지 않고 더 나은 청산 프로파일이 있는지 조사한다. 모두 true-rerun —
기존 run_sim 청산 플래그(stop/trail/max_hold)만 바꾼다. 진입/유니버스/스캐너/디시전/사이징/RiskGate 미변경.

잠긴 베이스라인(변경 금지): entry_fill_model next-bar-limit, buffer 0.03, stop 0.15, trailing 0.20,
max_holding 60, fractional. winner extension 미적용, **갭 가드 미적용**, next-open 미사용, 주말청산 빈 집합.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate·기본 유니버스·베이스라인 미변경.

## 변형 (청산 플래그만, 진입 잠금)
- baseline: stop 15 / trail 20 / hold 60.
- trail_off, trail_10, trail_15, trail_20, trail_25, trail_30 (stop 15 / hold 60).
- stop_off_trail20 (stop off / trail 20 / hold 60).
- stop_10_trailoff, stop_15_trailoff, stop_20_trailoff (trail off / hold 60).
- hold_45/60/75/90_trailoff (stop 15 / trail off).
- all_exits_off (stop·trail·hold 모두 off) — **diagnostic only**, best 후보에서 제외, 과대 주장 금지.

## 방법론 (정직)
- 미청산 포지션은 백테스트 끝에서 마지막 종가로 마크(exit_reason 'open', PnL은 미실현). avg holding days는
  청산된 leg만 집계.
- all_exits_off처럼 청산이 거의 없으면 소수 포지션이 끝까지 보유돼 왜곡된다 → diagnostic only로 표기하고
  best 후보 선정에서 제외.

## 측정(변형별)
- total PnL / cumulative return / MDD / return·MDD / win rate / trade count.
- avg·median 트레이드 PnL, average holding days, best/worst 심볼, top1·top3 PnL share, 분기 PnL.
- 청산 사유 count / PnL / avg PnL (time_stop / trailing_stop / stop_loss / other / open).
- beats SPY? / beats QQQ?
- 트레일링 영향: baseline vs trail_off 심볼별 PnL delta → 트레일링에 가장 손해/도움 본 심볼.

## 출력 — ExitDeepDive
- variants(ExitVariantResult), trailing_hurt/trailing_helped, best_by_ratio/best_by_pnl(diagnostic 제외),
  warnings, real_orders==0.

## 함수
- `compute_holding_days(legs) -> float|None`, `exit_reason_breakdown(legs) -> tuple[ExitReasonStat]`.
- `per_symbol_pnl(legs) -> dict`, `trailing_impact(base_legs, no_trail_legs, *, top) -> (hurt, helped)`.
- `summarize_variant(name, params, legs, performance, *, spy, qqq, diagnostic_only) -> ExitVariantResult`.
- `build_exit_deep_dive(variants, hurt, helped) -> ExitDeepDive`, `format_exit_deep_dive_markdown(report) -> str`.
- 러너 `experiments/exit_policy_deep_dive.py` (`python -m experiments.exit_policy_deep_dive`).

## 리포트가 답할 것 (정직)
- 트레일링이 일관되게 해치나, 소수 심볼에서만인가.
- 트레일링 비활성이 return/MDD를 개선하나, raw PnL만인가.
- 낮은 트레일링은 drawdown을 막지만 승자를 일찍 자르나.
- 높은 트레일링은 비활성과 비슷해지나.
- 트레일링 비활성 시 60일 max holding이 여전히 최선인가.
- 미래 잠금 베이스라인의 best 후보는.
- 지금 잠긴 베이스라인을 그대로 둬야 하나.

## 테스트 (tests/test_exit_deep_dive.py)
- 베이스라인 파라미터·기본 유니버스·run_sim 기본값 불변, 진입 잠금, 변형 파라미터 격리(기본 미변형).
- 청산 사유 attribution 카운트/평균 정확, holding days, trailing_impact.
- diagnostic_only 변형이 best 후보에서 제외·명확히 표기. 브로커/라이브 미사용, real_orders==0.

## 비범위
- 진입 모델/유니버스/스캐너/디시전/RiskGate 변경, winner extension/gap guard 적용, next-open, 라이브, 베이스라인 잠금 변경.
