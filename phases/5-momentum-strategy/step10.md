# Step 10: aggression-retune-rerun (MDD 예산 다 쓰기 + 재실행 + QQQ/SMH 게이트)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §6(예산 다 써라), §7(사이징), §9(QQQ/SMH 게이트·절대수익 점검), §10. 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-003 하드캡 / ADR-002)
- `/algorithms/sizing.py` (step 2), `/agents/v1_run.py` (step 7), `/algorithms/backtest.py` (step 5)
- step 8(측정)·step 9(레짐 v2)이 **선행 완료**돼야 한다.

## 작업

v1이 MDD를 9.8%만 써서(예산 20% 절반도 안 씀) **절대수익을 테이블에 남겼다.** 사이징을 공격적으로 올려
**설계 MDD(12~15%)를 쓰도록** 재튜닝하고, step 8·9 반영 상태로 **재실행**해 QQQ/SMH 대비 게이트를 본다.
⚠️ 공격성은 *20% 천장 안에서*, 그리고 *값은 편향 없는 데이터에서 확정*(편향 데이터 과튜닝 금지).

**SDD → TDD 순서를 강제한다.** (집계·캘리브 로직은 테스트, 실데이터 실행은 수동.)

### Step A. SPEC — `specs/sizing.md`·`specs/v1_run.md` (갱신)

- **공격성 상향**: `regime_adjusted_fraction`의 base `fraction`(및 A/B size_multiplier)을 올려 백테스트 MDD가 설계 12~15%에 닿도록. ADR-003 하드캡·20% 천장은 불변.
- **캘리브레이션 로직**: `calibrate_fraction` 갱신 — MDD < 12%면 "예산 미사용 → fraction 상향 제안", MDD > 15%면 "축소 제안"(양방향). 적용은 사람.
- **게이트 기준 변경(헌장 §9)**: 통과 = **QQQ·SMH 대비** 위험조정(Sharpe) 우위 + **절대수익이 인덱스에 크게 안 뒤짐** + 노출도 보고. SPY만으로 판정하지 않는다.
- V1Report에 노출도(time-in-market)·다중 벤치마크·캘리브 제안 포함.

### Step B. TEST (Red) — `tests/test_sizing.py`·`tests/test_v1_run.py` (갱신)

- `fraction` 상향 → 동일 데이터에서 MDD·총수익 단조 증가, **risk_amount는 여전히 ADR-003 하드캡 이내, MDD ≤ 20%**.
- `calibrate_fraction`: MDD 9.8% 입력 → 상향 제안. MDD 18% → 축소 제안.
- 게이트가 QQQ·SMH와 비교(SPY 단독 아님). 노출도 리포트.

### Step C. 구현 (Green) — `algorithms/sizing.py`, `agents/v1_run.py`

순수 집계는 algorithms. 공격성 파라미터는 노출(기본값 보수적, 캘리브로 조정 제안).

### Step D. 리팩터

캘리브·게이트 판정 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_sizing.py tests/test_v1_run.py -v
.venv/bin/python -m pytest -q
# 실데이터 재실행(수동): .venv/bin/python scripts/run_v1.py
```

## 검증 절차 / 게이트 판정 (사람이 함 — 헌장 §10)

1. 위 AC 실행(테스트는 mock). 실데이터 재실행은 수동.
2. 체크리스트: MDD가 9.8%→12~15%로 예산을 쓰는가? 총수익이 올랐는가? **QQQ/SMH 대비** 위험조정 우위인가(헌장 §9)? 노출도가 올랐는가(현금 비중↓)? 20% 천장·ADR-003 하드캡 안인가?
3. 핵심 질문(사람 판정): **"비용·편향 감안해도 QQQ/SMH를 위험조정으로 이기고, 절대수익도 부끄럽지 않은가?"**
   - 통과 → 생존편향 없는 데이터 재검증(헌장 §3) → 페이퍼 → 소액 라이브.
   - 실패 → 재튜닝, 또는 헌장 §0-5대로 인덱스(QQQ) 후퇴.
4. `phases/5-momentum-strategy/index.json`의 step 10 업데이트.

## 금지사항

- **20% MDD 천장·ADR-003 하드캡을 넘기지 마라.** 공격성은 그 안에서.
- ⚠️ **편향 든 데이터에서 공격성 값을 과튜닝하지 마라**(NVDA/AMD 불장 과적합 → 라이브 폭사). 값 확정은 편향 없는 데이터.
- **생존편향 제거 전 live greenlight 금지**(헌장 §3·§10). 실거래·실주문·자동 라이브 진입 코드 금지.
- SPY 단독으로 게이트 판정하지 마라(QQQ/SMH 필수). SPEC/TEST 없이 구현 금지.
