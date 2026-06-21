# SPEC: trend_leverage_experiment (추세 연장 + 레버리지 ETF 주말리스크 실험)

일반 모멘텀 승자가 더 긴 보유로 이득을 보는지, 레버리지 ETF는 더 엄격한 주말리스크 청산을 써야 하는지
점검한다. 기존 run_sim 로직으로 변형들을 돌려 비교한다(실험/리포트 러너). 기본 동작 불변, 전략/스캐너/
디시전/사이징/RiskGate 변경 없음. 레버리지 주말청산만 새 sim 기능(opt-in, 레버리지 심볼 전용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

## 베이스라인 설정(고정)
fractional, stop 0.15, trailing 0.20, max_holding 60, entry_fill_model next-bar-limit, buffer 0.03.

## 변형
1. `baseline_realistic`: max_holding 60.
2. `trend_extended_90`: max_holding 90(동일 stop/trailing/entry).
3. `trend_extended_120`: max_holding 120.
4. `winner_extension`(**report-only**): 60→90/120 연장은 포지션이 수익 + 추세 건강할 때만 — 안전 배선이
   어려우므로 우선 리포트. 베이스라인의 수익 time_stop 청산을 연장 후보로 식별하고(손실 포지션 제외),
   90/120 변형의 총손익 델타를 보고한다. 건강 조건(리포트용): 50MA 상회, 상대강도 양수, 추적스탑 미히트,
   레짐 risk-off 아님.
5. `leveraged_weekend_risk_shadow`(별도 유니버스): 레버리지 ETF(TQQQ/SQQQ/SOXL/UPRO/SPXL/SPXS/TECL/
   FNGU)만. 엄격 프로파일 stop 0.07, trailing 0.10, max_holding 20 + **주말 직전 강제청산**(레버리지 전용).
   일반주에 강요하지 않는다. 데이터 없으면 명확한 경고와 함께 skip.

## sim 지원(주말청산 — opt-in, 레버리지 전용)
- ExitPolicy.weekend_exit_symbols(frozenset). 비면 미적용(기본 불변). evaluate_exit에 weekend 사유
  (manual 다음 우선). DayInput.pre_weekend(주말 직전 거래일 — historical_sim가 거래일 시퀀스로 산출).
  multiday는 pre_weekend & 심볼∈weekend_exit_symbols일 때만 강제청산(ExitReason.WEEKEND_EXIT).

## 변형별 리포트(VariantResult)
cumulative_return, max_drawdown, win_rate, total_pnl, trades, avg_holding_days, longest_holding_days,
return_mdd_ratio, top_symbol/top_symbol_pnl_share, exit_reason 분포, weekend_exit 건수, real_orders_placed.

## 함수
- `run_variant(config, *, simulate_fn=None) -> VariantResult`.
- `compute_extension_candidates(legs) -> tuple` (수익 time_stop만 — 손실 제외).
- `run_trend_leverage_experiment(*, universe_root, leveraged_root=None, ...) -> ExperimentReport`.
- `format_*`. CLI: --universe-root/--benchmark/--symbols/--events-csv/--leveraged-root/--output.

## 테스트
- 베이스라인 불변(weekend 미설정 시 기본 동작 동일).
- 90/120 변형 실행(기본 안 바꿈).
- winner extension이 손실 포지션엔 적용 안 됨(후보=수익 time_stop만).
- 레버리지 주말청산이 레버리지 심볼에만 적용 / 일반주 영향 없음.
- 레버리지 데이터 없으면 안전 skip + 경고.
- real_orders_placed == 0.

## 비범위
- 조건부 연장의 실 sim 배선(보유 중 동적 연장), 레버리지 실매매, 분/틱 체결, 전략/시그널 변경.
