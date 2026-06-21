# SPEC: walk_forward (장기/워크포워드 검증 — 실험/리포트 전용)

잠긴 현실 베이스라인(next-bar-limit 3%, 60일)을 **긴 히스토리와 워크포워드 윈도우**에서 검증한다.
최근 2025-2026 AI/반도체 강세장 밖에서도 살아남는지 본다. 새 매매 경로 없음 — 기존 run_sim 시뮬을
날짜 윈도우별로 돌리고 baseline_comparison으로 벤치마크와 비교만 한다.

각 윈도우는 start_date만 바꿔 독립 백테스트로 돌린다(지표는 point-in-time `df.loc[:as_of]`로 윈도우
이전 히스토리까지 사용, 매매는 윈도우 안에서만). 잠긴 베이스라인 고정: entry_fill_model next-bar-limit,
buffer 0.03, max_holding 60, stop 0.15, trailing 0.20, fractional. winner extension 미적용, **갭 가드
미적용**, next-open 미사용. 레버리지 주말청산 opt-in 유지(일반주 미적용).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 스캐너/디시전/사이징/RiskGate 변경 없음. 기본 동작 불변.

## 윈도우 생성 (순수, 가용 데이터 범위 기준)
- full: 전체 가용 구간.
- yearly: 가용 구간과 교집합한 달력 연도.
- rolling 6-month: 6개월 윈도우, 3개월 스텝(겹침). 윈도우가 데이터에 완전히 들어갈 때만.
- rolling 12-month: 12개월 윈도우, 3개월 스텝.

## 윈도우별 처리
1. 잠긴 베이스라인으로 해당 [start,end] 재시뮬 → return/MDD/win/PnL/trades.
2. 벤치마크 비교(같은 윈도우): SPY / QQQ 매수보유, equal-weight 동일 유니버스(가능하면). 결측은 안전 처리.

## 워크포워드 요약 (rolling 윈도우 집합)
- 양수/음수 윈도우 수, best/worst 윈도우, 평균 return, 평균 MDD, return/MDD, worst drawdown.
- 한 강세 구간에만 통하는지(bull_dependent) 판정.

## 출력 — WalkForwardValidation
- full(WindowResult), yearly/rolling_6m/rolling_12m(tuple[WindowResult]).
- WindowResult: label/kind/start/end, return/MDD/win/PnL/trades, spy_return/qqq_return/eq_return,
  beats_spy/beats_qqq.
- summary(WalkForwardSummary), data_start/data_end, warnings, real_orders==0.

## 함수
- `generate_windows(data_min, data_max, *, yearly, roll6, roll12, step_months) -> tuple[Window]`.
- `make_window_result(label, kind, start, end, performance, benchmark_cmp) -> WindowResult` (순수).
- `compute_walk_forward_summary(results) -> WalkForwardSummary`.
- `build_walk_forward(full, yearly, roll6, roll12, summary, *, data_start, data_end) -> WalkForwardValidation`.
- `format_walk_forward(report) -> str`.
- 러너 `scripts/walk_forward.py`: 윈도우 생성 + 윈도우별 잠긴 베이스라인 재시뮬 + 벤치마크 비교.

## 테스트 (tests/test_walk_forward.py)
- 잠긴 베이스라인 설정 사용, next-open 미사용, winner extension 미사용.
- rolling 윈도우가 올바르게 생성(개수/경계/데이터 미달 시 빈 목록).
- 벤치마크 결측 안전 처리, equal-weight 결측 심볼 안전 처리.
- 리포트에 양수/음수 윈도우 수 포함. real_orders_placed == 0.

## 비범위
- 실 혼합 실행 sim, 자본 재배분 변경, 갭 가드/winner extension 적용, next-open 사용, 라이브, 전략/시그널/베이스라인 변경.
