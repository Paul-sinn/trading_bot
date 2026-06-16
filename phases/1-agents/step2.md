# Step 2: scanner-agent

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md` (스캐너: 워치리스트 순환 스캔, 시그널 후보 리스트업, 1분 주기)
- `/docs/ARCHITECTURE.md` (자동매매 루프: 스캐너 → 알고리즘 3레이어)
- `/agents/base.py`, `/specs/agent_base.md` (Agent 베이스)
- `/algorithms/signals.py`, `/algorithms/filters.py` (Layer 1·2 — 그대로 사용)
- `/specs/signals.md`, `/specs/filters.md`

## 작업

워치리스트를 순환하며 가격 데이터를 받아 알고리즘 Layer 1(시그널)+Layer 2(필터)를 적용해 **후보 종목 리스트**를 만드는 스캐너 에이전트를 구현한다.

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/scanner_agent.md`

- `PriceDataProvider` 인터페이스: `async def get_ohlcv(symbol: str) -> pd.DataFrame` (columns: open/high/low/close/volume). `MockPriceDataProvider`(결정론적 합성 데이터), 실거래 provider는 후속 phase.
- `Candidate(symbol: str, signal: Signal, filters_passed: bool, detail: dict)`.
- `ScannerAgent(Agent)`:
  - 생성자에 `AgentRegistry`, `PriceDataProvider`, `watchlist: list[str]`, (선택) `vix_provider`, `sentiment_provider` 주입.
  - `async def scan() -> list[Candidate]`: 워치리스트 각 심볼에 대해 signals.generate_signals + filters.apply_filters 적용. Layer1이 BULLISH이고 Layer2 통과한 종목만 후보로.
  - `async def tick()`: scan() 호출, 결과를 내부 `latest_candidates`에 저장. registry가 killed면 스캔 스킵.
- 엣지케이스: 빈 워치리스트, provider가 데이터 부족 DataFrame 반환, 일부 심볼 예외(다른 심볼 스캔은 계속 — 한 종목 실패가 전체를 막지 않게).

### Step B. TEST (Red) — `tests/test_scanner_agent.py`

- `MockPriceDataProvider`로 명백한 상승 추세 심볼 → 후보 포함.
- 약세/필터 탈락 심볼 → 후보 제외.
- 빈 워치리스트 → 빈 리스트.
- 한 심볼 provider 예외 시 나머지 심볼은 정상 스캔.
- registry.killed=True면 scan이 빈 결과/스킵.

### Step C. 구현 (Green) — `agents/scanner.py`

- algorithms의 순수 함수를 호출만 한다(재구현 금지). provider는 주입.
- `import talib` 금지.

### Step D. 리팩터

심볼별 평가 로직을 작은 함수로 분리.

## Acceptance Criteria

```bash
.venv/bin/python -m pytest tests/test_scanner_agent.py -v
.venv/bin/python -m pytest -q
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - 시그널/필터를 algorithms에서 재구현하지 않고 호출만 하는가?
   - 외부 데이터가 주입된 provider로만 들어오는가? (ADR-001/002)
   - 한 종목 실패가 전체 스캔을 막지 않는가?
3. `phases/1-agents/index.json`의 step 2를 업데이트한다 (completed/error/blocked + 필드).

## 금지사항

- EMA/RSI/MACD/ATR 등 지표를 scanner.py에서 다시 구현하지 마라. 이유: algorithms에 이미 있다(단일 진실). 호출만 하라.
- 실제 시세 API를 호출하지 마라. `MockPriceDataProvider`를 쓴다.
- 한 심볼의 예외로 전체 tick이 죽게 만들지 마라. 이유: 1분 주기 루프가 멈춘다.
- 기존 테스트를 깨뜨리지 마라.
