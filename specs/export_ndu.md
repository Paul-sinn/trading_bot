# SPEC: export_ndu CLI (NDU/Norgate SDK → gitignore된 CSV export)

NDU/Norgate에서 선택 심볼의 일봉을 받아 `scripts/run_sim.py`가 먹는 **gitignore된 로컬 CSV 폴더**로
저장하는 수동 export CLI. SDK 접근(I/O)은 여기서만 하고, 산출 CSV는 기존 `agents/norgate_bridge.py`가
그대로 로드할 수 있는 long-format이다.

관련: `agents/norgate_bridge.py`(load_norgate_folder가 산출물을 소비), `scripts/run_sim.py`(소비처).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. 전략 시그널 튜닝 없음. LLM/이벤트 캘린더 미연결.
로컬 CSV 데이터만 export. **시장 데이터는 커밋 금지** — 출력은 반드시 gitignore된 `data/` 아래.

CRITICAL (fail-closed, 가정 금지): NDU SDK 미설치/사용불가 → 명확한 `NduExportError`. 심볼 export 실패/
빈 응답/필수 컬럼 없음 → 심볼명을 담은 명확한 `NduExportError`. 출력이 `data/` 밖이면 거부.

## CSV 출력 포맷 (norgate_bridge 호환)
- 심볼당 한 파일 `<OUTPUT_DIR>/<SYMBOL>.csv`, 컬럼: `symbol,date,open,high,low,close,volume`.
- date는 `YYYY-MM-DD`. norgate_bridge가 symbol 컬럼을 그대로 인식(파일명 주입 불필요).

## CLI 인자
- `--symbols` (필수, nargs+): export할 심볼.
- `--output-dir` (선택, 기본 `data/ndu_export`): 출력 폴더. 반드시 `data/` 아래(아니면 거부).
- `--start-date` / `--end-date` (선택, YYYY-MM-DD): 기간(없으면 SDK 기본).
- `--overwrite` (선택, store_true): 기존 파일 덮어쓰기. 없으면 기존 파일 있을 때 거부(fail-closed).

## 함수
- `NduExportError(Exception)`: export 실패(fail-closed).
- `_load_ndu_provider(import_fn=...) -> module`: `norgatedata` 지연 import. 실패 시 NduExportError.
- `fetch_symbol_frame(symbol, start, end, *, provider) -> DataFrame`: provider로 일봉 조회 →
  7-컬럼 long-format. 실패/빈 응답/컬럼 누락 → NduExportError.
- `export_symbols(symbols, output_dir, *, start, end, overwrite, provider=None) -> list[Path]`:
  심볼별 CSV 저장. provider 없으면 `_load_ndu_provider()`. 파일별 검증.
- `_is_under_data(path) -> bool`: 경로가 repo `data/` 아래인지(CLI 안전 가드).

## 테스트 (tests/test_export_ndu.py) — **모킹된 NDU provider만, 실 NDU 불요**
- mock provider로 export → 심볼별 CSV 생성, 컬럼 == symbol,date,open,high,low,close,volume.
- 산출 폴더를 `load_norgate_folder`로 되읽어 심볼 일치(브리지 호환).
- `--overwrite` 없이 기존 파일 → NduExportError. 있으면 덮어씀.
- SDK 미설치(import 실패 주입) → NduExportError(명확한 메시지).
- 심볼 export 실패(provider가 raise) / 빈 응답 → 심볼명 담은 NduExportError.
- `data/` 밖 출력 → 거부.

## 비범위
- Norgate 워치리스트/지수편입 등 고급 SDK 기능, 가격 조정 정책(소스 책임), 전략/시그널/라이브.
