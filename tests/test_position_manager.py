"""Position & Exit Manager v0 테스트 — dry-run 청산만(실주문/매도 없음).

spec: specs/broker_snapshot_bridge.md
검증: 스냅샷에서 포지션 로드 + 미실현손익 계산, HOLD/STOP_LOSS/TRAILING_STOP/TIME_STOP,
missing quote 안전, 수동청산 감지, 모든 ExitDecision broker_order_id=None·real_order_placed=False·
real_orders_placed=0, API 읽기 전용. write 도구 없음.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.services.broker_snapshot import BrokerSnapshot, append_snapshot
import backend.app.services.position_manager as pm
from backend.app.services.position_manager import (
    Position,
    detect_manual_closes,
    evaluate_exit,
    make_manual_close_decision,
    read_positions,
    run_exit_cycle,
)
from backend.app.main import app


def _snap(positions, quotes, ts="2026-06-22T15:00:00+00:00") -> BrokerSnapshot:
    return BrokerSnapshot(timestamp=ts, buying_power=1000.0, positions=positions, quotes=quotes)


# --- 포지션 로드 + 미실현손익 ---
def test_read_positions_and_unrealized_pnl(tmp_path):
    append_snapshot(
        _snap(
            [{"symbol": "NVDA", "quantity": 2.0, "average_buy_price": 100.0}],
            [{"symbol": "NVDA", "price": 110.0}],
        ),
        reports_dir=tmp_path,
    )
    pos = read_positions(reports_dir=tmp_path)
    assert len(pos) == 1
    p = pos[0]
    assert p.symbol == "NVDA" and p.quantity == 2.0 and p.average_buy_price == 100.0
    assert p.current_quote == 110.0
    assert p.market_value == 220.0
    assert p.unrealized_pnl == pytest.approx(20.0)
    assert p.unrealized_pnl_pct == pytest.approx(0.10)
    assert p.entry_source == "broker_snapshot" and p.status == "open"


def test_read_positions_empty_when_no_snapshot(tmp_path):
    assert read_positions(reports_dir=tmp_path) == []


# --- 청산 규칙 ---
def _pos(**kw) -> Position:
    base = dict(symbol="NVDA", quantity=2.0, average_buy_price=100.0, current_quote=100.0,
                peak_price=100.0, holding_days=1)
    base.update(kw)
    return Position(**base)


def test_hold_when_no_rule():
    assert evaluate_exit(_pos(current_quote=98.0, peak_price=100.0)).exit_signal == "HOLD"


def test_stop_loss_triggered():
    d = evaluate_exit(_pos(current_quote=80.0))  # -20% <= -15%
    assert d.exit_signal == "STOP_LOSS" and d.would_sell_quantity == 2.0


def test_trailing_stop_triggered():
    d = evaluate_exit(_pos(average_buy_price=100.0, current_quote=150.0, peak_price=200.0))  # -25% from peak
    assert d.exit_signal == "TRAILING_STOP" and d.would_sell_quantity == 2.0


def test_time_stop_triggered():
    d = evaluate_exit(_pos(current_quote=105.0, peak_price=105.0, holding_days=60))
    assert d.exit_signal == "TIME_STOP"


def test_stop_loss_takes_priority_over_time():
    d = evaluate_exit(_pos(current_quote=80.0, holding_days=99))
    assert d.exit_signal == "STOP_LOSS"


def test_missing_quote_handled_safely():
    d = evaluate_exit(_pos(current_quote=None))
    assert d.exit_signal == "HOLD" and "quote" in d.reason


def test_missing_avg_handled_safely():
    d = evaluate_exit(_pos(average_buy_price=None, current_quote=100.0))
    assert d.exit_signal == "HOLD"


# --- 불변식: 실주문/매도 흔적 0 ---
@pytest.mark.parametrize("signal_pos", [
    dict(current_quote=80.0),       # STOP_LOSS
    dict(current_quote=98.0),       # HOLD
    dict(current_quote=150.0, peak_price=200.0),  # TRAILING_STOP
])
def test_exit_decision_invariants(signal_pos):
    d = evaluate_exit(_pos(**signal_pos))
    assert d.broker_order_id is None
    assert d.real_order_placed is False
    assert d.real_orders_placed == 0


# --- 수동 청산 감지 ---
def test_manual_close_detected_from_previous_snapshot(tmp_path):
    append_snapshot(_snap([{"symbol": "NVDA", "quantity": 2.0, "average_buy_price": 100.0}],
                          [{"symbol": "NVDA", "price": 110.0}], ts="2026-06-22T15:00:00+00:00"),
                    reports_dir=tmp_path)
    append_snapshot(_snap([], [], ts="2026-06-22T16:00:00+00:00"), reports_dir=tmp_path)  # NVDA 사라짐
    gone = detect_manual_closes(reports_dir=tmp_path)
    assert len(gone) == 1 and gone[0]["symbol"] == "NVDA"
    d = make_manual_close_decision(gone[0])
    assert d.exit_signal == "MANUAL_CLOSE_DETECTED"
    assert d.would_sell_quantity == 0.0  # 이미 사라짐 — 주문 없음
    assert d.broker_order_id is None and d.real_orders_placed == 0


def test_run_exit_cycle_persists(tmp_path):
    append_snapshot(_snap([{"symbol": "NVDA", "quantity": 2.0, "average_buy_price": 100.0}],
                          [{"symbol": "NVDA", "price": 80.0}]), reports_dir=tmp_path)
    decisions = run_exit_cycle(reports_dir=tmp_path)
    assert any(d.exit_signal == "STOP_LOSS" for d in decisions)
    raw = (tmp_path / "exit_decisions.jsonl").read_text(encoding="utf-8")
    assert '"real_orders_placed": 0' in raw
    assert '"real_order_placed": false' in raw
    assert '"broker_order_id": null' in raw


# --- API 읽기 전용 ---
@pytest.fixture
def reports(tmp_path, monkeypatch):
    import backend.app.services.broker_snapshot as bs
    monkeypatch.setattr(pm, "DEFAULT_REPORTS_DIR", tmp_path)
    monkeypatch.setattr(bs, "DEFAULT_REPORTS_DIR", tmp_path)
    return tmp_path


def test_api_positions_and_exits(reports):
    append_snapshot(_snap([{"symbol": "NVDA", "quantity": 2.0, "average_buy_price": 100.0}],
                          [{"symbol": "NVDA", "price": 80.0}]), reports_dir=reports)
    run_exit_cycle(reports_dir=reports)

    client = TestClient(app)
    pos = client.get("/api/positions").json()
    assert len(pos) == 1 and pos[0]["symbol"] == "NVDA"

    latest = client.get("/api/exits/latest").json()
    assert latest["exit_signal"] == "STOP_LOSS"
    assert latest["broker_order_id"] is None
    assert latest["real_order_placed"] is False
    assert latest["real_orders_placed"] == 0

    lst = client.get("/api/exits?limit=50").json()
    assert len(lst) >= 1


def test_api_empty_safe(reports):
    client = TestClient(app)
    assert client.get("/api/positions").json() == []
    assert client.get("/api/exits/latest").json() is None
    assert client.get("/api/exits").json() == []
