# SPEC: execution_stress (진입 실행 슬리피지 + 갭 스트레스 진단 — 리포트 전용)

next-bar-limit 3% vs next-open을 **실행비용(슬리피지) + 갭 추격 리스크** 하에서 비교한다. 두 실행의 트레이드
결과에 슬리피지/갭가드를 **사후 적용**(재시뮬 없음, 원 결과 불변)한다. 라이브/기본 전략을 바꾸지 않는다.

베이스라인 잠금: max_holding 60, stop 0.15, trailing 0.20, fractional. winner extension 미적용. 레버리지
주말청산 opt-in 유지(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 리포트 전용.

## 입력
- `limit3_diag`, `next_open_diag`: 3% limit / next-open 실행의 TradeDiagnostics. `price_data`(갭). `starting_cash`.

## 스트레스
- 슬리피지(진입가 가산): 0 / 0.10% / 0.25% / 0.50% / 1.00%. adj_pnl = pnl − entry_price×slip×qty.
- 갭 추격 가드(next-open 전용): 진입 갭(open/직전종가−1) > 임계(3%/5%/8%)면 그 진입 skip(트레이드 제거).

## 조합
- limit3 × 5 슬리피지(가드 없음).
- next-open × 5 슬리피지(가드 없음).
- next-open × 가드(3/5/8%) (대표 슬리피지 0.25%에서).

## 조합별 리포트 (StressResult)
policy, slippage_pct, gap_guard(|None), cumulative_return(=total/cash), max_drawdown_proxy(청산일순 누적손익
낙폭), win_rate, total_pnl, trades, avg_holding_days, return_mdd_ratio, top_symbol/top_symbol_pnl_share,
skipped_gap_entries, skipped_profitable_pnl, real_orders_placed == 0(property).

## 함수
- `compute_execution_stress(limit3_diag, next_open_diag, price_data, *, starting_cash=1000.0) -> StressReport`.
- `format_execution_stress(report) -> str`.
- 러너 `scripts/entry_execution_stress.py`: 3% limit + next-open 두 시뮬을 60일 베이스라인 고정으로 돌려 호출.
- StressReport: results, best_by_return_mdd, warnings, real_orders_placed == 0.

## 테스트 (tests/test_execution_stress.py)
- 스트레스 그리드가 두 정책 모두 실행.
- 슬리피지가 PnL을 낮추되 원 diag 불변.
- 갭 가드가 임계 초과 진입을 skip + skipped 보고.
- 베이스라인 60 / 기본 불변(러너) / real_orders_placed == 0.

## 비범위
- 실제 슬리피지/마켓임팩트 모델, 분/틱 체결, 실 혼합 실행 sim, 라이브 적용, 전략/시그널 변경, 베이스라인 변경.
