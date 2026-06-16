# SPEC: backend_server

FastAPI 백엔드 서버의 최소 골격. REST 초기 로드(`/health`)와 실시간 push(WebSocket
`/ws/ticker`)를 제공한다. 외부 시세 연동은 이 step 범위 밖이므로 **결정론적 mock
가격 생성기**를 사용한다 (실제 Robinhood MCP 연동은 step 4 이후).

관련 문서: ARCHITECTURE(통신 규약 — REST 초기로드 / WebSocket 실시간 push),
ADR-001(프론트/백 분리), ADR-003(외부 API는 backend에만 격리).

## 엔드포인트

### GET /health
- **입력**: 없음.
- **출력**: `200 OK`, body `{"status": "ok"}`.
- **용도**: 헬스 체크 / 프론트 초기 연결 확인.

### WebSocket /ws/ticker
- **입력**: 연결 수립. (쿼리 파라미터 `symbols`는 선택 — 미지정 시 기본 워치리스트 사용.)
- **출력**: 연결 직후부터 주기적으로 가격 스냅샷 JSON을 server→client push.
- **메시지 스키마**:
  ```json
  {
    "type": "ticker",
    "data": {
      "<symbol>": { "price": 123.45, "ts": "2026-06-15T09:00:00+00:00" }
    }
  }
  ```
  - `type`: 항상 문자열 `"ticker"`.
  - `data`: 심볼 → `{price, ts}` 매핑. `price`는 float, `ts`는 ISO 8601 문자열.
- **push 주기**: 설정 가능한 상수 `TICKER_INTERVAL_SECONDS` (기본 1.0초, 테스트는 짧게 override).
- **가격 생성**: 결정론적 mock. 심볼별 기준가 + 의사난수(심볼+tick seed) 워크.
  실제 시세/외부 API 호출 없음.

## 기본 워치리스트
- `["AAPL", "TSLA", "NVDA"]` (mock 기준가 보유). 빈 워치리스트도 허용한다.

## 엣지케이스
- **클라이언트 비정상 종료**: `WebSocketDisconnect` 발생 시 push 루프를 정상 종료한다
  (좀비 태스크/무한 루프 방지). 예외를 잡아 루프를 break 한다.
- **빈 워치리스트**: `symbols`가 비면 `data`가 빈 객체 `{}`인 ticker 메시지를 push한다
  (크래시하지 않음).
- **잘못된 경로**: 정의되지 않은 경로는 `404`.

## 비범위 (이 step에서 하지 않음)
- 실제 Robinhood/외부 시세 API 호출.
- DB 영속화, Redis 캐시, 인증.
- 포트폴리오/주문/설정/리포트 라우트 (후속 step).
