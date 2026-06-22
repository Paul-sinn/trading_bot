"""`/api/broker` 읽기 전용 엔드포인트 테스트.

spec: specs/robinhood_mcp_readonly.md
검증: snapshot/snapshots 엔드포인트는 파일만 읽고 MCP를 호출하지 않는다. 부재 시 안전(null/[]),
계정번호 마스킹 유지, real_orders_placed=0. 워커 스크립트가 살균 스냅샷을 적재한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.app.services.broker_snapshot as bs
from backend.app.main import app

client = TestClient(app)


@pytest.fixture
def reports(tmp_path, monkeypatch):
    # 엔드포인트가 기본 reports_dir를 읽으므로 모듈 기본 경로를 tmp로 바꾼다(격리).
    monkeypatch.setattr(bs, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_snapshot_empty_returns_null(reports):
    resp = client.get("/api/broker/snapshot")
    assert resp.status_code == 200
    assert resp.json() is None  # 스냅샷 없음 → null(크래시 없음)


def test_snapshots_empty_returns_list(reports):
    resp = client.get("/api/broker/snapshots?limit=10")
    assert resp.status_code == 200
    assert resp.json() == []


def test_snapshot_returns_latest_masked(reports):
    bs.append_snapshot(bs.BrokerSnapshot(account_last4="••••9372", cash=1.0), reports_dir=reports)
    bs.append_snapshot(bs.BrokerSnapshot(account_last4="••••9372", cash=2.0), reports_dir=reports)
    body = client.get("/api/broker/snapshot").json()
    assert body["cash"] == 2.0  # 최신
    assert body["account_last4"] == "••••9372"
    assert body["real_orders_placed"] == 0


def test_snapshots_list_and_limit(reports):
    for i in range(3):
        bs.append_snapshot(bs.BrokerSnapshot(cash=float(i)), reports_dir=reports)
    body = client.get("/api/broker/snapshots?limit=2").json()
    assert [s["cash"] for s in body] == [1.0, 2.0]  # 최근 2건


def test_worker_build_from_raw_writes_masked(reports):
    # 워커가 쓰는 빌더 경로: 원본 → 살균 → append. 전체 계정번호 미저장.
    raw = {
        "accounts": {"data": {"accounts": [{"account_number": "778689372", "agentic_allowed": True}]}},
        "portfolio": {"data": {"cash": "1000", "buying_power": {"buying_power": "1000"}}},
    }
    snap = bs.build_snapshot_from_raw(raw)
    bs.append_snapshot(snap, reports_dir=reports)
    raw_file = (reports / "broker_snapshots.jsonl").read_text(encoding="utf-8")
    assert "778689372" not in raw_file
    assert "••••9372" in raw_file
    body = client.get("/api/broker/snapshot").json()
    assert body["account_last4"] == "••••9372"
    assert body["buying_power"] == 1000.0
