# SPEC: backtest (v1 일봉 백테스트 엔진)

헌장 `docs/STRATEGY.md` §10: 백테스트는 헌장 전략을 과거 일봉에 재생해 **§10 검증 사다리**를 가능케 하고,
산출(승률·손익비·표본수)을 Kelly 콜드스타트 입력으로 공급한다. v1 = **일봉 전용 완전 전략**(진입+청산+
사이징+레짐+비용) → go/no-go 게이트. 1시간봉(v2)은 범위 밖.

관련 문서: `docs/STRATEGY.md` §1/§6/§7/§7-2/§8/§9/§10(최상위 권위), `tasks/backtest-engine-prompt.md`(상세),
ADR-002, ADR-006, step0~4 순수 함수(signals/regime/entry/exits/sizing).

CRITICAL: **부수효과 없는 순수 함수, 결정론적**(같은 입력→같은 출력). I/O·네트워크·난수·전역상태 금지.
데이터는 입력 DataFrame만 소비(로딩은 step6/agents). step0~4를 **재구현하지 않고 호출**(단일 진실).
CRITICAL: **미래참조(look-ahead) 금지.** 바 t 의사결정은 t까지 데이터만. 신호=일봉 종가, **체결=다음날 시가**.
워밍업(200d MA 등) 전 거래 제외. CRITICAL: 비용·슬리피지를 0으로 두지 않는다(일봉 체결 낙관 → 부풀림 방지).
CRITICAL: LLM/센티먼트 실호출 금지 — v1은 결정론적 가격 엣지만 측정.

## 입력 가정
- `price_data: dict[symbol -> DataFrame]`, `spy_df`, `vix_series`는 **같은 날짜 인덱스로 정렬**됐다고 가정
  (동일 길이·동일 순서). 각 DataFrame은 open/high/low/close 컬럼 보유.

## 설정 dataclass (frozen)
```python
CostModel:   slippage_bps=5.0(0 금지), commission=0.0   # Robinhood 무수수료여도 PFOF 슬리피지 반영
BacktestParams: initial_capital=100_000, entry_mode="pullback"|"breakout", max_risk_pct=0.01,
    base_fraction=1.0(콜드스타트 고정비율, 켈리 미사용), atr_period=14, atr_stop_mult=2.0,
    trail_atr_mult=3.0, warmup=200, periods_per_year=252, price_col="close",
    fast=50, slow=200, rs_lookback=63, short_ma=20, pullback_window=5, breakout_lookback=20
ExitLayers:  use_breakeven=True, use_partial=True, use_trailing=True,
    use_regime_exit=True, use_time_stop=True, use_pre_earnings=False   # 백테스트 기본 실적캘린더 없음
```
- `base_fraction`: 거래기록 0(콜드스타트) → 켈리 미신뢰 → 고정 비율 사이징(헌장 §7). 백테스트가 산출하는
  win_rate/win_loss_ratio가 이후 `effective_kelly_fraction` 입력이 된다(라이브 전환 시).
- `ExitLayers`는 evaluate_exit 토글로 전달 → **청산 레이어 A/B 검증**(베이스라인 ①+④ → +② → +③ → +⑤⑦).

## 결과 dataclass (frozen)
```python
Trade: symbol, entry_idx, exit_idx, entry_price, exit_price(가중평균), qty, pnl(비용後 순), return_pct,
       regime_at_entry: str, reason
Benchmark: sharpe, cagr, max_drawdown   # SPY 매수후보유(동일 기간)
RegimePerformance: regime: str, trades: int, total_pnl: float
BacktestResult: total_trades, wins, losses, win_rate, win_loss_ratio(avg_win/avg_loss, 0분모 안전),
       sharpe(주지표), sortino, max_drawdown(분수), total_return, cagr, profit_factor, expectancy,
       benchmark: Benchmark(=benchmarks["SPY"], 하위호환), benchmarks: dict[str, Benchmark],
       time_in_market_pct: float, avg_concurrent_positions: float,
       trades: list[Trade], regime_breakdown: list[RegimePerformance],
       survivorship_warning: str   # ⚠️ 항상 채움(아래)
```

### step8 갱신 (측정 결함 교정 — 헌장 §6/§9)
- **CAGR 정의 고정(총수익과 수학적 일관)**: `cagr = (1 + total_return) ** (1 / years) - 1`,
  `years = len(equity_window) / periods_per_year`. 전략·**모든 벤치마크에 동일 정의** 적용(불일치 버그 방지).
  total_return=0 → cagr=0. (역산: 주어진 total_return·years로 정확히 재현 가능해야 한다.)
- **다중 벤치마크(헌장 §9 — 기술주 유니버스라 SPY만으론 부족)**: `run_backtest(..., benchmark_data: dict[str, DataFrame] | None)`.
  결과 `benchmarks: dict[str, Benchmark]`에 **SPY(항상) + benchmark_data의 각 심볼(QQQ·SMH 등)** 매수후보유
  (CAGR·Sharpe·MDD)를 담는다. `benchmark`(단수)는 `benchmarks["SPY"]`로 하위호환 유지.
- **노출도(time-in-market) 측정** — "현금에 앉아 Sharpe만 샀는지" 드러냄:
  - `time_in_market_pct` = (보유 포지션≥1인 봉수 / 평가창 봉수) ∈ [0,1]. 항상 보유→1.0, 거래 0→0.0.
  - `avg_concurrent_positions` = 평가창 봉당 평균 동시 보유 포지션 수.
  - 평가창 = `equity_curve[warmup:]`와 동일 구간(봉별 `len(positions)`를 같은 구간에서 집계).
- **생존편향 경고(필수)**: `survivorship_warning`에 "v1은 현재 유니버스+무료데이터 → 생존편향 내장 →
  낙관적 상한. fail-fast 용도지 라이브 greenlight 아님. 라이브 전 생존편향 없는 벤더 재검증 필요(헌장 §3)."

## 함수

### `run_backtest(price_data, spy_df, vix_series, *, params=BacktestParams(), costs=CostModel(), exit_layers=ExitLayers()) -> BacktestResult`
바별 루프(t = 0..n-1):
1. 종가 t로 equity 마크(cash + Σ 보유 qty×close[t]) → equity_curve.
2. `t < warmup` 또는 `t >= n-1`(다음날 시가 없음)이면 거래 판정 스킵.
3. `regime = classify_regime(spy_close[:t+1], vix[t])` (step1, 미래참조 없음).
4. **청산 먼저**(보유 포지션): `Bar(high,low,close)[t]` + `evaluate_exit(..., regime, days_held=t-entry_idx,
   atr=atr[t], **exit_layers)`. `sell_fraction>0` → **다음날 시가 t+1**에 체결(매도=open×(1−slippage)).
   부분이면 qty 감소·스탑 갱신, 전량/잔량0이면 Trade 확정(pnl=proceeds−cost_basis, 비용 반영).
5. **진입**(미보유 심볼, 정렬 순회): `pullback_entry`/`breakout_entry`(entry_mode) — df·spy를 `[:t+1]`로
   슬라이스(미래참조 차단). enter면 **다음날 시가 t+1**에 체결(매수=open×(1+slippage)). 초기스탑=fill−atr×mult,
   `kelly_f=regime_adjusted_fraction(base_fraction, regime)`, `position_size(equity,...)`로 수량. cash 부족 시 스킵.
- 지표: equity_curve[warmup:]로 sharpe(연율 √252, rf=0)·sortino(하방 std)·max_drawdown·total_return·cagr.
  trades로 win_rate·win_loss_ratio·profit_factor·expectancy(전부 0분모 안전). SPY는 spy_close[warmup:] 매수후보유.

### `walk_forward(price_data, spy_df, vix_series, *, train_frac=0.6, params, costs, exit_layers) -> tuple[BacktestResult, BacktestResult]`
- `split=int(n×train_frac)`. train = 데이터 `[:split]` 백테스트. test = 전체 데이터 + `warmup=max(warmup, split)`
  (split 이전 전체를 워밍업으로 두고 `[split:]`만 거래) → 아웃샘플. 같은 params(재적합 없음 — OOS 열화 측정, 헌장 §10②).

## 엣지케이스 / 불변
- 0거래(워밍업 부족·신호 없음·레짐 C/D): total_trades=0, win_rate=0, sharpe=0(0분모 폭발 금지), 예외 없음.
- 결정론: 동일 입력 2회 → 동일 결과(심볼 정렬 순회).
- 미래참조 차단: 미래 바를 바꿔/추가해도 그 이전에 확정된 거래·지표 불변(각 판정이 `[:t+1]`만 사용).
- 비용↑ → total_return·win_rate 단조 악화(개선 불가).
- 레짐 D 전 구간 → 신규 진입 0.
- `win_rate`·`win_loss_ratio`·`total_trades` → `effective_kelly_fraction` 입력 시 항상 `[0, cap]`.

## 비범위
- 1시간봉(v2), 데이터 로딩/실시간(step6/agents), 실주문/슬리피지 실측(executor), 상관·집중도 포트폴리오 최적화,
  생존편향 없는 벤더 재검증(라이브 전, 별도). v1 숫자를 라이브 greenlight로 해석 금지(헌장 §3).
