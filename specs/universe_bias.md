# SPEC: universe_bias (유니버스 확장/편향 테스트 — 실험/리포트 전용)

현재 잠긴 20-심볼 AI/반도체/빅테크 유니버스가 너무 손수 고른(handpicked) 것은 아닌지 본다. 같은 잠긴
베이스라인 파라미터로 유니버스만 바꿔 비교한다. 새 매매 경로 없음 — run_sim 시뮬을 유니버스별로 돌리고
robustness_report/baseline_comparison으로 측정만 한다.

잠긴 베이스라인 고정(변경 금지): 기본 20-심볼 유니버스 그대로, entry_fill_model next-bar-limit,
buffer 0.03, max_holding 60, stop 0.15, trailing 0.20, fractional. winner extension 미적용,
**갭 가드 미적용**, next-open 미사용. 레버리지 주말청산 opt-in 유지(빈 집합). 스캐너/디시전/사이징/
RiskGate 변경 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 확장 유니버스는 실험 전용 — 프로덕션 기본값 미변경.

## 유니버스 변형
- baseline: 잠긴 기본 주식 유니버스(레버리지 ETF 미포함).
- expanded: 40-60 후보(반도체/소프트웨어/AI 인프라 확장). 로컬 데이터에 있는 심볼만 거래, 없는 심볼은
  스킵하고 리포트.
- expanded_no_mu: expanded에서 MU 제외(MU 의존도 점검).
- expanded_no_top3: expanded에서 baseline 상위 3 PnL 심볼 제외(집중도 점검).

## 데이터 한계 (정직)
- 확장 후보 중 로컬 데이터가 없는 심볼은 거래 불가 → 스킵·리포트. 데이터가 baseline 20개뿐이면 expanded는
  사실상 baseline과 동일해진다(과대 주장 금지). 인프라는 데이터가 추가되면 그대로 동작한다.
- 레버리지 ETF(TQQQ/SOXL/UPRO/SQQQ/FNGU 등)는 이 일반 유니버스 테스트에 섞지 않는다.

## 변형별 측정
- cumulative return / total PnL / MDD / return/MDD / win rate / trade count.
- top1·top3 심볼 PnL share, best/worst 심볼, 분기 PnL.
- missing(스킵된 심볼), zero-trade 심볼.
- SPY/QQQ 매수보유, equal-weight 바스켓 비교.

## 출력 — UniverseBiasReport
- variants(UniverseResult), warnings, real_orders==0.
- UniverseResult: name/requested/present/missing/zero_trade, return/pnl/mdd/ret_over_mdd/win/trades,
  top1·top3 share, best/worst, quarterly, spy/qqq/eq return + beats.

## 함수
- `summarize_universe(name, requested, present, performance, robustness, benchmark_cmp) -> UniverseResult`.
- `compute_top_shares(symbol_perf) -> (top1, top1_share, top3, top3_share, best, worst)`.
- `build_universe_bias(variants) -> UniverseBiasReport`.
- `format_universe_bias_markdown(report) -> str` (reports/universe_bias_test.md 용).
- 러너 `experiments/universe_bias_test.py` (`python -m experiments.universe_bias_test`).

## 테스트 (tests/test_universe_bias.py)
- 기본 베이스라인 유니버스 상수 불변, 확장은 실험 전용, run_sim 기본값 불변.
- 레버리지 ETF가 확장 유니버스에 없음. weekend_exit_symbols 기본 빈 집합.
- missing 심볼 스킵·리포트, zero-trade 심볼 리포트.
- 러너가 잠긴 베이스라인 파라미터 사용, 브로커/라이브 경로 미사용, real_orders_placed == 0.

## 비범위
- 실 혼합 실행, 자본 재배분 변경, 갭 가드/winner extension 적용, next-open 사용, 라이브, 베이스라인/전략 변경, 데이터 신규 수집.
