# Step 5: backtest-engine (v1 일봉 백테스트 엔진 — 미래참조 차단·비용·워크포워드)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §9(벤치마크), §10(검증 사다리·v1 일봉·정직성 규칙), §6(목표·MDD). 충돌 시 헌장이 진실.
- `/tasks/backtest-engine-prompt.md` — **이 step의 상세 스펙(§5 엔진 요구·§6 산출지표·§7 테스트).**
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-002)
- `/algorithms/signals.py`·`regime.py`·`entry.py`·`exits.py`·`sizing.py` (step 0~4 — 엔진이 오케스트레이션)

## 작업

step 0~4의 순수 함수를 오케스트레이션해 **v1 일봉 백테스트 엔진**을 만든다(`tasks/backtest-engine-prompt.md` 구현).
**미래참조 차단**(신호=일봉 종가, 체결=다음날 시가)·보수적 비용·워크포워드·`BacktestResult`(Sharpe·MDD·SPY 비교)
가 핵심. 결정론적 순수 함수.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/backtest.md`

- `run_backtest(price_data: dict[str, DataFrame], spy_df, vix_series, *, params, costs, exit_layers) -> BacktestResult`.
- 바별 루프: 각 일봉 종가에 step0~4 호출 → 진입/청산 판정 → **다음날 시가 체결**. 워밍업 구간 제외.
- 비용: 진입/청산마다 슬리피지(bps)+수수료 차감(보수적, 기본 슬리피지 0 금지).
- 청산 레이어 on/off 파라미터(`exit_layers`)로 A/B 검증 가능(베이스라인 ①+④ → +② → +③ → +⑤⑦).
- 워크포워드: train/test split 평가 함수.
- `BacktestResult`(frozen): total_trades/wins/losses/win_rate/win_loss_ratio/**sharpe**/sortino/max_drawdown/total_return/cagr/profit_factor/expectancy + **SPY 벤치마크(sharpe·cagr·mdd)** + 거래리스트 + 레짐별 분해.
- ⚠️ 산출에 **생존편향 경고** 명기(v1은 무료데이터+현재 유니버스 → "낙관적 상한", 라이브 greenlight 아님).

### Step B. TEST (Red) — `tests/test_backtest.py`

- **미래참조 차단**: 마지막 바 이후 가격 변경 → 과거 거래·지표 불변.
- 결정론(2회 동일). 비용↑ → total_return·win_rate 단조 악화.
- 상승추세 합성 → 양의 expectancy / 톱니 → 저조. 레짐 D 구간 신규진입 0.
- 0거래 안전(0분모 없이 win_rate=0). SPY 벤치마크 산출. 결과→`effective_kelly_fraction` `[0,cap]`.
- 청산 레이어 on/off가 결과를 바꾸는지(A/B 동작 확인).

### Step C. 구현 (Green) — `algorithms/backtest.py`

순수 함수(ADR-002, I/O 금지 — 데이터는 입력 DataFrame). step0~4 **호출**(재구현 금지). LLM/센티먼트는
주입형, 기본 중립 mock(실호출 금지).

### Step D. 리팩터

루프·체결·지표·벤치마크 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_backtest.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행. 특히 미래참조 차단·결정론 테스트.
2. 체크리스트: 신호=종가/체결=다음날 시가인가? 비용이 보수적으로 반영됐는가? SPY 위험조정 비교가 산출되는가(헌장 §9)? 청산 레이어 A/B가 가능한가? 생존편향 경고가 명기됐는가? step0~4를 재구현 없이 호출하는가?
3. `phases/5-momentum-strategy/index.json`의 step 5 업데이트.

## 금지사항

- 미래참조(look-ahead): 같은 바 종가 보고 같은 바 체결, 미래 데이터 참조 금지.
- 비용·슬리피지 0으로 두지 마라(부풀림). step0~4 재구현 금지. LLM 실호출 금지.
- I/O·네트워크·데이터 조회 금지(입력 DataFrame만). 1시간봉(v2)은 범위 밖.
- SPEC/TEST 없이 구현 금지. v1 숫자를 "라이브 가능" 신호로 해석하지 마라(생존편향 — 헌장 §3).
