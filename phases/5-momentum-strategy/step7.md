# Step 7: v1-run-evaluate (v1 백테스트 실행 → 매매일지·지표 → go/no-go 게이트)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §6(목표·MDD)·§9(승리 정의)·§10(검증 사다리·게이트·v1). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md`
- `/algorithms/backtest.py` (step 5), `/agents/data_adapter.py` (step 6)

## 작업

step 6 어댑터로 무료 일봉을 받아 step 5 엔진으로 **v1 백테스트를 실행**하고, **매매일지 + 성과 리포트**를
산출하는 실행 스크립트/서비스를 만든다. 그리고 헌장 §10 게이트 판정에 필요한 수치를 정리한다. ⚠️ **go/no-go
최종 판정은 사람이 한다** — 이 step은 *판단 근거(숫자)*를 만든다.

**SDD → TDD 순서를 강제한다.** (실행 스크립트라도 핵심 집계 로직은 테스트.)

### Step A. SPEC — `specs/v1_run.md`

- `run_v1(universe, start, end, params) -> V1Report`: 어댑터로 데이터 로드 → 엔진 실행 → 리포트 조립.
- `V1Report`: 전략 지표(Sharpe·Sortino·MDD·CAGR·승률·profit factor·expectancy) vs **SPY 벤치마크 나란히**,
  거래 일지(진입/청산/손익/사유/보유일), 레짐별 분해, **청산 레이어 A/B 결과**(베이스라인 ①+④ → +② → +③ → +⑤⑦).
- `fraction`(켈리) 캘리브레이션: MDD 설계 ≤15% 목표에 맞춰 `fraction`을 조정한 결과 보고(헌장 §6·§7).
- **게이트 체크리스트 출력**(헌장 §10): 비용後 Sharpe, MDD, SPY 대비 위험조정 우위 여부 — pass/fail 표시(판정 보조).
- ⚠️ 리포트 상단에 **생존편향 경고**(v1=낙관적 상한, 라이브 greenlight 아님 — 라이브 전 생존편향 없는 벤더 재검증 필요).

### Step B. TEST (Red) — `tests/test_v1_run.py`

- `MockDailyProvider` 합성 데이터로 `run_v1` → V1Report 조립 검증(네트워크 없이).
- SPY 벤치마크가 리포트에 포함. 청산 레이어 A/B가 각각 산출. 게이트 체크리스트 pass/fail 로직.
- 생존편향 경고가 리포트에 존재.

### Step C. 구현 (Green) — `scripts/run_v1.py` (+ 집계 로직은 backend/agents)

- 실행 스크립트 + 테스트 가능한 순수 집계 분리. 결과를 사람이 읽을 형식(표/JSON)으로.

### Step D. 리팩터

로드·실행·집계·리포트 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_v1_run.py -v
.venv/bin/python -m pytest -q
# (실데이터 실행은 수동: .venv/bin/python scripts/run_v1.py — 네트워크 필요, CI 아님)
```

## 검증 절차

1. 위 AC 실행(테스트는 mock). 실데이터 실행은 수동.
2. 체크리스트: SPY 위험조정 비교가 나란히 나오는가(헌장 §9)? 청산 레이어 A/B가 보고되는가? MDD가 게이트(≤15설계/≤20하드)와 대조되는가? 생존편향 경고가 상단에 있는가? 게이트 판정이 *사람 몫*으로 남고 자동 라이브 진입이 없는가?
3. `phases/5-momentum-strategy/index.json`의 step 7 업데이트.

## 게이트 판정 (사람이 함 — 헌장 §10)

- 핵심 질문: **"비용 차감 後, 일봉 모멘텀이 SPY를 위험조정(Sharpe)으로 이기는가? MDD가 한도 내인가?"**
- **통과** → 다음(생존편향 없는 데이터 재검증 → 페이퍼 → 소액 라이브). **실패** → 전략 수정 후 재실행, 또는 헌장 §0-5대로 인덱스 후퇴.

## 금지사항

- 이 step에서 **실거래·실주문·자동 라이브 진입 금지**(연구·측정 단계).
- v1 결과만으로 라이브 greenlight 결론 내리지 마라(생존편향 — 헌장 §3·§10).
- 테스트에서 네트워크 호출 금지(mock). SPEC/TEST 없이 구현 금지.
