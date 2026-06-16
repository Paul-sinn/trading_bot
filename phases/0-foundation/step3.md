# Step 3: backend-server

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md` (통신 규약: REST 초기로드, WebSocket 실시간 push)
- `/docs/ADR.md` (ADR-001: 프론트/백 분리)
- `/backend/app/core/config.py` (step 0 산출물)

이전 step의 backend 구조를 읽고 일관성을 유지하라.

## 작업

**SDD → TDD 순서를 강제한다.**

### Step A. SPEC — `specs/backend_server.md`

입력/출력/엣지케이스를 정의한다. 최소 포함 내용:
- `GET /health` → `{"status": "ok"}` (200).
- WebSocket `/ws/ticker` → 연결 시 주기적으로 가격 스냅샷 JSON을 push. 메시지 스키마: `{"type": "ticker", "data": {"<symbol>": {"price": float, "ts": iso8601}}}`.
- 엣지케이스: 클라이언트 비정상 종료(연결 끊김 처리), 빈 워치리스트, 잘못된 경로(404).

### Step B. TEST (Red) — `tests/test_backend_server.py`

FastAPI `TestClient`(및 websocket 테스트 지원)로 작성. 구현 전에는 실패해야 한다.
- `GET /health` 200 + body 검증.
- WebSocket `/ws/ticker` 연결 후 최소 1개 ticker 메시지 수신, 스키마 검증.
- 존재하지 않는 경로 404.

### Step C. 구현 (Green) — `backend/app/`

- `backend/app/main.py` — FastAPI 앱, 라우터 등록, CORS(로컬 frontend 허용).
- `backend/app/api/health.py` — `/health` 라우터.
- `backend/app/ws/ticker.py` — `/ws/ticker` WebSocket 엔드포인트.
  - 이 step에서는 실제 시세 대신 **결정론적 mock 가격 생성기**를 사용한다 (예: 기준가 + 의사난수). 실제 Robinhood 연동은 step 4와 후속 phase.
  - push 주기는 설정 가능한 상수(테스트에서 빠르게). 기본 1초.
- 시그니처 예:
  ```python
  # backend/app/ws/ticker.py
  async def ticker_stream(ws: WebSocket, symbols: list[str]) -> None: ...
  ```

### Step D. 리팩터

테스트 통과 유지하며 구조 정리(가격 생성기를 별도 함수로 분리 등).

## Acceptance Criteria

```bash
pytest tests/test_backend_server.py -v
python -c "from backend.app.main import app; print([r.path for r in app.routes])"
```

(서버 수동 기동 확인은 선택: `uvicorn backend.app.main:app` 후 `curl localhost:8000/health`)

## 검증 절차

1. 위 AC 커맨드를 실행한다. 테스트가 모두 통과해야 한다.
2. 아키텍처 체크리스트:
   - 외부 API 호출이 backend에만 있는가 (frontend로 새지 않았는가)?
   - WebSocket 메시지 스키마가 spec과 일치하는가?
   - ADR 스택(FastAPI)을 벗어나지 않았는가?
3. `phases/0-foundation/index.json`의 step 3을 업데이트한다:
   - 성공 → `"completed"` + `"summary"` (생성한 엔드포인트/스키마 요약)
   - 실패 → `"error"` + `"error_message"`
   - 개입 필요 → `"blocked"` + `"blocked_reason"`

## 금지사항

- 실제 Robinhood/외부 시세 API를 호출하지 마라. 이유: 키가 없고 이 step 범위 밖이다. 결정론적 mock을 쓴다.
- SPEC/TEST 없이 구현부터 작성하지 마라. 이유: ADR-006 SDD→TDD 강제 위반.
- WebSocket 무한 루프에 종료 조건(연결 끊김 예외 처리)을 빠뜨리지 마라. 이유: 좀비 태스크가 쌓인다.
- 기존 테스트를 깨뜨리지 마라.
