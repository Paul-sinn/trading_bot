# Step 11: survivorship-free-revalidation (생존편향 없는 데이터 재검증 인프라)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §3(룰 기반 point-in-time·생존편향 없는 벤더·상폐종목), §9(QQQ/SMH), §10(검증 사다리 ②). 충돌 시 헌장이 진실.
- `/CLAUDE.md`, `/docs/ADR.md` (ADR-001 I/O 격리 / ADR-002 순수)
- `/agents/data_adapter.py`, `/specs/data_adapter.md` (step6 — DailyDataProvider 패턴)
- `/algorithms/backtest.py`, `/agents/v1_run.py` (step5/7/10 — run_backtest/run_v1·게이트)

## 작업

v1 숫자는 **생존편향 내장(낙관적 상한)**이라 라이브 greenlight가 아니다(헌장 §3). 생존편향 없는
데이터로 재검증할 **인프라**를 만든다. ⚠️ 실제 벤더 데이터(Norgate급)는 유료라 자격증명이 필요 →
코드는 **소스 비종속**(CSV/Parquet 드롭인 1순위 + Norgate SDK 지연 import 스켈레톤)으로 짜고,
**point-in-time 유니버스(상폐종목 포함, 미래 미참조)**와 **약세장(2018/2022) OOS 검증 러너**를 구현한다.
working fraction은 **보수적 max_risk_pct≈0.015**. 실제 greenlight 런은 사용자가 데이터 파일을 꽂아야 한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/universe.md`(신규)·`specs/oos_validate.md`(신규)·`specs/data_adapter.md`(갱신)

- `algorithms/universe.py`(순수): `select_universe(metrics: dict[str, SymbolMetrics], as_of, *, min_dollar_volume, atr_pct_band, exclude_leveraged=True) -> list[str]`.
  - `SymbolMetrics`(frozen): `listed_from`, `delisted_at: str|None`, `avg_dollar_volume`, `atr_pct`, `is_leveraged_or_inverse`.
  - point-in-time 규칙(헌장 §3): as_of ∈ [listed_from, delisted_at) AND 유동성≥min AND ATR%∈band AND 레버리지/인버스 제외.
    상폐종목도 **상폐 이전 시점엔 포함**(생존편향 제거), 미상장은 제외(미래참조 금지).
- `agents/data_adapter.py`: `PointInTimeProvider` Protocol(`get_constituents(as_of)->list[str]`, `get_ohlcv(symbol,start,end)` 상폐종목 포함, `get_metrics(as_of)->dict[str,SymbolMetrics]`).
  - `MockPointInTimeProvider`(결정론, **상폐종목+약세장 포함** 합성, 네트워크 없음).
  - `CsvPointInTimeProvider`(로컬 CSV/Parquet 드롭인 — 네트워크 없음, 1순위 실데이터 경로).
  - `NorgateProvider`(지연 import 스켈레톤 — 미설치/미구독 시 명확한 안내, 실호출 안 함).
- `agents/oos_validate.py`: `run_oos_validation(provider, windows: dict[str,(start,end)], *, max_risk_pct=0.015, ...) -> dict[str, V1Report]`.
  - 각 윈도우(예: `2018-bear`, `2022-bear`, `full-oos`)마다 point-in-time 유니버스로 run_v1 실행. 보수적 fraction.

### Step B. TEST (Red) — `tests/test_universe.py`·`tests/test_oos_validate.py`·`tests/test_data_adapter.py`(갱신)

- `select_universe`: 상폐종목이 상폐 *전* as_of엔 포함, *후*엔 제외. 미상장(listed_from 이후) 제외. 레버리지 제외. 유동성·ATR 밴드 필터. 미래참조 없음.
- `MockPointInTimeProvider`: 상폐종목·약세장 데이터 제공, 네트워크 없음. Protocol 준수.
- `run_oos_validation`: 윈도우별 V1Report dict 산출, max_risk_pct=0.015 적용, 네트워크 없이 mock으로.

### Step C. 구현 (Green)

순수 선정은 `algorithms/universe.py`(ADR-002). I/O(provider·러너)는 `agents/`(ADR-001). 실 SDK 지연 import.

### Step D. 리팩터

선정·provider·OOS 러너 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_universe.py tests/test_oos_validate.py tests/test_data_adapter.py -v
.venv/bin/python -m pytest -q
# 실데이터 재검증(수동): 사용자가 CSV/Norgate 데이터 꽂은 뒤 oos_validate 실행 — 네트워크/유료, CI 아님
```

## 검증 절차 / 게이트 (사람이 함 — 헌장 §10 ②)

1. 위 AC 실행(테스트는 mock·네트워크 0).
2. 체크리스트: point-in-time가 상폐종목 포함·미래 미참조인가? provider가 소스 비종속(주입형)인가? OOS 러너가 약세장(2018/2022) 윈도우를 도는가? working fraction 보수적(0.015)인가? algorithms 순수·I/O 격리인가?
3. 핵심 질문(사람 판정, 실데이터 후): **"생존편향 제거·약세장 포함 後에도 QQQ를 위험조정으로 이기는가?"** 통과 → 페이퍼 → 소액 라이브. 실패 → 헌장 §0-5대로 인덱스(QQQ) 후퇴.
4. `phases/5-momentum-strategy/index.json`의 step 11 업데이트.

## 금지사항

- **미래참조 금지**: 미상장 종목을 as_of 유니버스에 넣지 마라. 상폐종목을 상폐 후 시점에 넣지 마라.
- **생존편향 재유입 금지**: 상폐종목을 빼지 마라(그게 편향의 원인).
- 테스트에서 실제 네트워크·유료 SDK 호출 금지(mock/CSV/monkeypatch). 실 SDK는 지연 import.
- **생존편향 제거·OOS 통과 전 live greenlight 금지**(헌장 §3·§10). 실거래·자동 라이브 진입 코드 금지.
- 편향 데이터 과튜닝 값 그대로 쓰지 마라 — working fraction 보수적(≈0.015), 값 확정은 이 단계 데이터에서.
- algorithms/에 I/O 금지(ADR-002). SPEC/TEST 없이 구현 금지.
