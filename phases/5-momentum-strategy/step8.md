# Step 8: measurement-fixes (CAGR 일관성 + QQQ/SMH 벤치마크 + 노출도 측정)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §9(벤치마크 = SPY+QQQ+SMH, 절대수익 점검), §6(MDD 예산), §10. 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002)
- `/algorithms/backtest.py`, `/specs/backtest.md` (step 5 — BacktestResult·Benchmark)
- `/agents/v1_run.py`, `/specs/v1_run.md` (step 7 — V1Report)

## 작업

v1 측정의 결함을 고친다. ① **CAGR ↔ 총수익 일관성**(v1 리포트 CAGR ~14%인데 총수익 +169.75%면 ~10년 기준 ~10%여야 함 → 버그 의심) ② **벤치마크를 SPY뿐 아니라 QQQ·SMH도** (기술주 유니버스라 SPY만으론 부족) ③ **노출도(time-in-market) 측정** — "현금에 앉아 Sharpe만 샀는지" 드러내기. 순수 함수 유지.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/backtest.md`·`specs/v1_run.md` (갱신)

- **CAGR 정의 고정**: `cagr = (1 + total_return) ** (1 / years) - 1`, `years = 거래일수 / 252`(또는 실제 달력연수). 총수익과 수학적으로 일관. 같은 정의를 전략·벤치마크에 동일 적용.
- **다중 벤치마크**: `BacktestResult`/`V1Report`가 **SPY·QQQ·SMH** 각각의 buy-and-hold (CAGR·Sharpe·MDD)를 산출. `benchmarks: dict[str, Benchmark]`.
- **노출도 지표**: `time_in_market_pct`(포지션 보유 봉수 / 전체 봉수), `avg_concurrent_positions`. BacktestResult에 추가.
- 입력: QQQ·SMH 가격 시리즈를 run_backtest/run_v1에 주입(데이터는 step 6 어댑터가 공급).

### Step B. TEST (Red) — `tests/test_backtest.py`·`tests/test_v1_run.py` (갱신)

- 알려진 총수익+기간 → CAGR이 `(1+ret)^(1/years)-1`과 정확히 일치(역산 검증). 총수익 0 → CAGR 0.
- 벤치마크 dict에 SPY·QQQ·SMH 3개 모두 존재, 각 CAGR/Sharpe/MDD 산출.
- `time_in_market_pct` ∈ [0,1]. 항상 보유 → 1.0, 거래 0 → 0.0.

### Step C. 구현 (Green) — `algorithms/backtest.py`, `agents/v1_run.py`

순수 집계는 algorithms/backtest(ADR-002). v1_run은 어댑터로 QQQ·SMH 로드해 주입.

### Step D. 리팩터

CAGR·벤치마크·노출도 산출 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_backtest.py tests/test_v1_run.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행.
2. 체크리스트: CAGR이 총수익과 수학적으로 일관한가(역산 일치)? SPY·QQQ·SMH 3개 벤치마크가 다 나오는가(헌장 §9)? 노출도가 측정되는가? 순수 함수인가?
3. `phases/5-momentum-strategy/index.json`의 step 8 업데이트.

## 금지사항

- CAGR을 총수익과 다른 시간 base로 계산하지 마라(불일치 버그 재발). 전략·벤치마크 동일 정의.
- SPY 하나만 벤치마크하지 마라 — QQQ·SMH 필수(헌장 §9, 기술주 유니버스).
- I/O를 algorithms/에 넣지 마라(벤치마크 데이터 로드는 v1_run/어댑터). SPEC/TEST 없이 구현 금지.
