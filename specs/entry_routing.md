# SPEC: entry_routing (진입 실행 라우팅 진단 — 리포트 전용)

진입 실행을 심볼의 갭 행태로 라우팅해야 하는지 본다: 저갭 심볼 → next-bar-limit 3%, 고갭 모멘텀 심볼 →
next-open. **리포트/진단 전용** — 라이브/기본 전략을 바꾸지 않는다. 라우팅 PnL은 두 실행(3% limit /
next-open)의 심볼별 PnL을 합친 **what-if 근사**(단일 $현금 포트폴리오 시뮬 아님).

베이스라인 잠금: max_holding 60, stop 0.15, trailing 0.20, fractional. winner extension 미적용. 레버리지
주말청산 opt-in 유지(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음.

## 입력
- `limit3_diag`, `next_open_diag`: 각각 3% limit / next-open 실행의 TradeDiagnostics(심볼별 leg+pnl).
- `price_data`: {symbol: OHLC}(갭 통계용). `starting_cash`.

## 심볼별 분석 (SymbolRouting)
- 갭(진입 바 open / 직전 종가 − 1): avg_gap, median_gap, gap_up_freq, large_gap_up_freq_2pct/3pct, n_gaps.
- limit3_pnl, next_open_pnl, diff(next_open − limit3), missed_profitable_count/pnl(3% limit 누락 수익 근사).
- is_high_gap(large_gap_up_freq_2pct ≥ threshold), prefers("next_open"/"limit").

## 라우팅 정책(what-if 근사)
1. `all_limit_3pct`: 전 심볼 3% limit.
2. `all_next_open`: 전 심볼 next-open.
3. `gap_routed_conservative`: 고갭 심볼만 next-open, 나머지 3% limit.
4. `gap_routed_aggressive`: 진단상 next-open이 3% limit을 능가한 심볼만 next-open — **overfit 위험/진단 전용**.

## 정책별 메트릭 (RoutedPolicyResult)
total_pnl, cumulative_return(=total/cash), trades, win_rate, max_drawdown_proxy(청산일순 누적손익 낙폭),
return_mdd_ratio, top_symbol/top_symbol_pnl_share, is_diagnostic_only.

## 경고
- aggressive는 항상 진단 전용(사후 승자 선택) 경고.
- 라우팅 이득이 한 심볼에 집중(양수 diff 합의 60%+)이면 경고.

## 함수
- `compute_symbol_gap_stats(df, entry_dates) -> GapStats`.
- `compute_entry_routing(limit3_diag, next_open_diag, price_data, *, starting_cash=1000.0, high_gap_threshold=0.25) -> RoutingReport`.
- `format_entry_routing(report) -> str`.
- 러너 `scripts/entry_routing_diagnostics.py`: 3% limit + next-open 두 시뮬을 돌려 compute_entry_routing 호출.

## 테스트 (tests/test_entry_routing.py)
- 갭 통계 정확. 저갭/고갭 분류. conservative가 고갭→next_open / 저갭→limit 선택.
- aggressive가 사후 승자 선택 + 진단 전용 라벨. all_limit/all_next_open 합계.
- 입력 불변. 베이스라인/기본 불변(러너 설정 잠금). real_orders_placed == 0.

## 비범위
- 실제 혼합 실행 sim(심볼별 라우팅 체결), 정확한 자본 재배분, 라이브 적용, 전략/시그널 변경, 베이스라인 변경.
