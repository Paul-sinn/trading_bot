"""`/api/shadow` 라우터 테스트 (spec: specs/shadow_view.md).

GET /api/shadow[?date=] → ShadowReportView. POST /api/shadow/run {date?} → 고정 커맨드 +
엄격 검증된 --date만. CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed=0.
잘못된 날짜 형식은 거부(주문 경로 없음 — 임의 인자 주입 차단).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import backend.app.api.shadow as shadow_api
from backend.app.main import app

client = TestClient(app)


def test_get_shadow_returns_view():
    res = client.get("/api/shadow")
    assert res.status_code == 200
    body = res.json()
    assert "available" in body
    assert body["real_orders_placed"] == 0       # 불변식


def test_get_shadow_accepts_date_query():
    res = client.get("/api/shadow", params={"date": "2026-06-18"})
    assert res.status_code == 200
    assert res.json()["real_orders_placed"] == 0


def test_run_rejects_malformed_date(monkeypatch):
    # subprocess가 절대 호출되지 않아야 한다(잘못된 날짜 → 즉시 거부).
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("subprocess는 잘못된 날짜에 호출되면 안 된다")

    monkeypatch.setattr(shadow_api.subprocess, "run", _boom)
    res = client.post("/api/shadow/run", json={"date": "not-a-date; rm -rf /"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["real_orders_placed"] == 0
    assert called["n"] == 0                        # 실행 안 됨


def test_run_command_has_no_broker_path():
    # 고정 커맨드는 일간 섀도 리포트 모듈만 — 브로커/Robinhood/주문 경로 없음.
    cmd = " ".join(shadow_api._DAILY_CMD)
    assert "experiments.daily_shadow_report" in cmd
    for forbidden in ("robinhood", "broker", "order", "mcp"):
        assert forbidden not in cmd.lower()


def test_run_valid_date_passes_date_flag(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(shadow_api.subprocess, "run", _fake_run)
    res = client.post("/api/shadow/run", json={"date": "2026-06-18"})
    assert res.status_code == 200
    assert res.json()["real_orders_placed"] == 0
    assert "--date" in captured["cmd"]
    assert "2026-06-18" in captured["cmd"]
