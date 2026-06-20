# SPEC: run_sim CLI (NDU/Norgate export → historical_sim 성과 리포트)

NDU/Norgate가 export한 로컬 CSV 폴더를 받아 `historical_sim` 전체 파이프라인을 돌리고 성과 리포트를
출력/저장하는 **얇은 수동 CLI**. 로직은 전부 기존 모듈(norgate_bridge/price_csv/historical_sim/
perf_report) 재사용. 이 파일은 인자 파싱 + 배선 + 출력만 한다.

관련: `agents/norgate_bridge.py`(load_norgate_folder), `agents/price_csv.py`(close_series,
DataAdapterError), `agents/historical_sim.py`(run_historical_simulation), `agents/perf_report.py`
(format_performance_report).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM 미연결.
이벤트 캘린더 실연동 없음. 전략 시그널 튜닝 없음.

CRITICAL (fail-closed, 가정 금지): 데이터 폴더 없음/CSV 없음/필수 컬럼 누락/벤치마크·컴퍼스(SPY)
심볼 없음/거래일 0 → 추정하지 않고 명확한 `DataAdapterError` 메시지로 비정상 종료(exit code 2).

## 데이터 폴더
- `--data-root`는 라이선스상 **gitignore된 로컬 폴더**(`data/`)여야 한다(커밋 금지). NDU export의
  심볼별 CSV(`NVDA.csv` …)를 `load_norgate_folder`로 로드.
- 컴퍼스/레짐: `SPY`(고정) 종가가 폴더에 있어야 한다(없으면 fail-closed).
- 벤치마크: `--benchmark`(기본 SPY) 종가가 폴더에 있어야 한다(없으면 fail-closed).
- VIX: 폴더에 `VIX` 심볼이 있으면 그 종가, 없으면 중립 상수(15.0) 시리즈(레짐만, 거래 판단 아님).
- 거래 유니버스: `--symbols` 주면 그 목록(폴더에 없으면 fail-closed), 없으면 `SPY/VIX/벤치마크`를
  제외한 전 심볼.

## CLI 인자
- `--data-root` (필수): NDU/Norgate export CSV 폴더(로컬, gitignore).
- `--symbols` (선택, nargs=*): 거래 대상 심볼. 비면 전체(보조 심볼 제외).
- `--start-date` / `--end-date` (선택, YYYY-MM-DD): 거래일 범위. start 비면 warmup 이후부터.
- `--starting-cash` (선택, 기본 1000): 시작 현금.
- `--benchmark` (선택, 기본 SPY): 벤치마크 심볼.
- `--warmup` (선택, 기본 200): start 미지정 시 건너뛸 초기 바 수(컨텍스트 형성 여유).
- `--assume-no-events` (선택, store_true): 드라이런 편의 — 이벤트 리스크 없음 가정
  (MockEventRiskProvider). 기본 off면 이벤트 게이트가 fail-closed로 전 후보 veto. **실 캘린더 아님.**
- `--output` (선택): 성과 리포트 텍스트 저장 경로(UTF-8). 콘솔에도 항상 출력.

## 동작
1. `load_norgate_folder(data_root)` → dict[symbol, OHLCV] (없음/오류 → DataAdapterError).
2. SPY/벤치마크/VIX/유니버스 분리(없으면 fail-closed).
3. 거래일 = SPY 인덱스 ∩ [start,end] (start 비면 [warmup:]). 0개면 fail-closed.
4. `run_historical_simulation(...)` (event_provider는 --assume-no-events일 때만 Mock(True)).
5. `format_performance_report` 출력 + (`--output` 시) 저장. exit 0.

## 테스트 (tests/test_run_sim.py)
- fixture CSV 폴더로 드라이런 → 성과 리포트 생성, real orders 0.
- 데이터 폴더 없음 → SystemExit(코드 2), 명확한 메시지.
- 벤치마크 심볼 없음 → SystemExit(코드 2), 명확한 메시지.
- `--output` 지정 시 리포트 파일 생성.
- real_orders_placed == 0.

## 비범위
- Norgate SDK 라이브 로드(NDU on), 이벤트 캘린더, LLM, 라이브 주문, 전략/시그널 변경.
