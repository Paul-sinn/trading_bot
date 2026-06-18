# Step 6: data-adapter-daily (무료 일봉 OHLCV 로더 — I/O)

## 읽어야 할 파일

- `/docs/STRATEGY.md` — **최상위 권위.** §3(데이터 소스 — 무료 일봉 v1·생존편향·Robinhood=실행만), §10(v1). 충돌 시 헌장이 진실.
- `/CLAUDE.md` (외부 API·I/O는 backend/agents에만, ADR-001), `/docs/ADR.md`
- `/algorithms/backtest.py` (step 5 — 이 어댑터가 공급할 DataFrame 형식 확인)
- `/agents/scanner.py` (PriceDataProvider 주입 패턴 — 같은 스타일 따르라)

## 작업

백테스트(step 5)와 라이브가 쓸 **일봉 OHLCV 데이터 어댑터**를 만든다. ⚠️ 이건 **I/O라 순수 함수가 아니다** —
`agents/`(또는 `backend/`)에 둔다(ADR-001/002). 무료 일봉 소스(yfinance/Stooq 등)에서 OHLCV + SPY + VIX를
받아 백테스트 엔진이 먹는 DataFrame 형식으로 정규화한다. **소스는 주입형(provider)** — 엔진/테스트는 소스에 안 묶인다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/data_adapter.md`

- `DailyDataProvider` 인터페이스: `get_ohlcv(symbol, start, end) -> DataFrame`(컬럼: open/high/low/close/volume, 수정주가), `get_vix(start, end) -> Series`.
- `FreeDailyProvider(DailyDataProvider)`: 무료 소스(yfinance/Stooq 등) 실연동. 네트워크 호출은 여기에만.
- `MockDailyProvider`: 결정론적 합성 데이터(테스트용, 네트워크 없음).
- 정규화: 결측·분할/배당 조정·타임존·반장(half-day) 처리. 캐싱(중복 조회 회피) 선택.
- ⚠️ **생존편향 명시**: 무료 소스는 상장폐지 종목이 없어 생존편향 내장 → v1 한정, 메타에 경고 플래그.

### Step B. TEST (Red) — `tests/test_data_adapter.py`

- **네트워크 호출 절대 금지**: `MockDailyProvider` 또는 fake/monkeypatch로 검증.
- 정규화 결과가 백테스트 엔진 기대 형식과 일치(컬럼·dtype·정렬).
- 결측/이상치 처리. 인터페이스 준수(Protocol).

### Step C. 구현 (Green) — `agents/data_adapter.py` (또는 backend service)

- 실제 SDK import는 지연 import(미설치 환경에서 전체가 안 죽게). 네트워크는 provider 내부에만.
- CRITICAL: 시크릿·키를 로그에 노출하지 마라.

### Step D. 리팩터

조회·정규화·캐싱 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_data_adapter.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 실행(네트워크 없이).
2. 체크리스트: I/O가 agents/backend에 격리됐는가(ADR-001)? 소스가 주입형이라 엔진이 안 묶이는가? 테스트가 네트워크 없이 도는가? 생존편향 경고가 명기됐는가? 정규화 형식이 step 5와 일치하는가?
3. `phases/5-momentum-strategy/index.json`의 step 6 업데이트.

## 금지사항

- 테스트에서 실제 네트워크 호출 금지(CI 실패·비결정). fake/monkeypatch.
- `algorithms/`에 I/O를 넣지 마라(ADR-002 — 순수 함수 격리). 이건 agents/backend.
- Robinhood 과거 데이터를 백테스트 소스로 쓰지 마라(짧고 미조정, 헌장 §3).
- 지연 import 안 쓰고 최상단 import로 미설치 시 전체를 깨뜨리지 마라. SPEC/TEST 없이 구현 금지.
