"""Step 3 backend_server 테스트 (TDD Red→Green).

spec: specs/backend_server.md
- GET /health → 200 {"status": "ok"}
- WebSocket /ws/ticker → ticker 메시지 push, 스키마 검증
- 잘못된 경로 → 404
"""

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_path_returns_404():
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404


def test_ws_ticker_pushes_valid_message():
    with client.websocket_connect("/ws/ticker") as ws:
        msg = ws.receive_json()

    assert msg["type"] == "ticker"
    assert isinstance(msg["data"], dict)
    # 기본 워치리스트가 비어있지 않으므로 최소 1개 심볼.
    assert len(msg["data"]) >= 1
    for symbol, quote in msg["data"].items():
        assert isinstance(symbol, str)
        assert isinstance(quote["price"], float)
        assert isinstance(quote["ts"], str)
        # ts는 ISO 8601 파싱 가능해야 한다.
        from datetime import datetime

        datetime.fromisoformat(quote["ts"])


def test_ws_ticker_empty_watchlist_pushes_empty_data():
    with client.websocket_connect("/ws/ticker?symbols=") as ws:
        msg = ws.receive_json()

    assert msg["type"] == "ticker"
    assert msg["data"] == {}
