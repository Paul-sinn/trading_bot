"""Discord 알림 테스트 — 매매 이벤트 webhook(backend 전용).

검증: URL 없으면 no-op(전송/파일 없음) · 카테고리 토글 · 이벤트별 embed 색/제목 · dedupe ·
전송 실패 흡수 · 시크릿/전체계정/URL 미노출 · 알림은 주문을 내지 않음(real_orders_placed 불변).
실제 네트워크 없음(post 주입/monkeypatch).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
import backend.app.services.discord_notifier as dn
from backend.app.services.discord_notifier import (
    GREEN, RED, AMBER,
    notify_exit, notify_order_receipt, notify_real_execution, send_test,
)
from backend.app.services.order_receipt import OrderReceipt
from backend.app.services.position_manager import ExitDecision
from backend.app.services.real_order_executor import RealExecutionReceipt
from backend.app.main import app


class Spy:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.ok


def _cfg(**kw) -> Settings:
    base = dict(discord_webhook_url="https://discord.test/webhook/SECRET")
    base.update(kw)
    return Settings(**base)


def _nourl() -> Settings:
    # 명시적 None — 로컬 .env에 DISCORD_WEBHOOK_URL이 있어도 무시(hermetic).
    return Settings(discord_webhook_url=None)


def _real(decision="REAL_SUBMITTED", side="BUY", **kw) -> RealExecutionReceipt:
    base = dict(intent_id="i", idempotency_key="i", symbol="PLTR", side=side, decision=decision,
                limit_price=120.0, notional=25.0, quantity=0.208)
    base.update(kw)
    return RealExecutionReceipt(**base)


def _exit(signal="STOP_LOSS") -> ExitDecision:
    return ExitDecision(symbol="PLTR", quantity=0.2, average_buy_price=120.0, current_price=100.0,
                        unrealized_pnl_pct=-0.16, exit_signal=signal, reason="r", would_sell_quantity=0.2)


def _order(decision="WOULD_SUBMIT") -> OrderReceipt:
    return OrderReceipt(intent_id="k", idempotency_key="k", symbol="PLTR", side="BUY",
                        decision=decision, notional=20.0)


# --- URL 없으면 no-op ---
def test_no_url_is_noop(tmp_path):
    spy = Spy()
    assert notify_real_execution(_real(), settings=_nourl(), reports_dir=tmp_path, post=spy) is False
    assert notify_exit(_exit(), settings=_nourl(), reports_dir=tmp_path, post=spy) is False
    assert notify_order_receipt(_order(), settings=_nourl(), reports_dir=tmp_path, post=spy) is False
    assert spy.calls == []
    assert not (tmp_path / "discord_sent.jsonl").exists()  # 파일도 안 건드림


# --- 이벤트별 embed ---
def test_real_buy_is_green(tmp_path):
    spy = Spy()
    assert notify_real_execution(_real(side="BUY"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    emb = spy.calls[0][1]["embeds"][0]
    assert emb["color"] == GREEN and "실매수" in emb["title"]


def test_real_sell_is_red(tmp_path):
    spy = Spy()
    notify_real_execution(_real(side="SELL"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    assert spy.calls[0][1]["embeds"][0]["color"] == RED


def test_real_blocked_amber_and_gated_by_blocks_toggle(tmp_path):
    spy = Spy()
    # blocks 토글 off → 미전송
    assert notify_real_execution(_real(decision="REAL_BLOCKED"), settings=_cfg(discord_notify_blocks=False), reports_dir=tmp_path, post=spy) is False
    assert spy.calls == []
    # on → amber 전송
    assert notify_real_execution(_real(decision="REAL_BLOCKED"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    assert spy.calls[0][1]["embeds"][0]["color"] == AMBER


def test_real_orders_toggle_off_blocks_send(tmp_path):
    spy = Spy()
    assert notify_real_execution(_real(), settings=_cfg(discord_notify_real_orders=False), reports_dir=tmp_path, post=spy) is False
    assert spy.calls == []


def test_exit_stop_loss_red_hold_skipped(tmp_path):
    spy = Spy()
    assert notify_exit(_exit("STOP_LOSS"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    assert spy.calls[0][1]["embeds"][0]["color"] == RED
    # HOLD는 노이즈 — 미전송
    assert notify_exit(_exit("HOLD"), settings=_cfg(), reports_dir=tmp_path, post=spy) is False


def test_exit_toggle_off(tmp_path):
    spy = Spy()
    assert notify_exit(_exit(), settings=_cfg(discord_notify_exits=False), reports_dir=tmp_path, post=spy) is False


def test_order_receipt_would_submit_green_gated(tmp_path):
    spy = Spy()
    assert notify_order_receipt(_order("WOULD_SUBMIT"), settings=_cfg(discord_notify_dry_run_intents=False), reports_dir=tmp_path, post=spy) is False
    assert notify_order_receipt(_order("WOULD_SUBMIT"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    assert spy.calls[0][1]["embeds"][0]["color"] == GREEN


def test_order_receipt_blocked_gated_by_blocks(tmp_path):
    spy = Spy()
    assert notify_order_receipt(_order("BLOCKED"), settings=_cfg(discord_notify_blocks=False), reports_dir=tmp_path, post=spy) is False
    assert notify_order_receipt(_order("BLOCKED"), settings=_cfg(), reports_dir=tmp_path, post=spy)


# --- dedupe ---
def test_dedupe_same_event_once(tmp_path):
    spy = Spy()
    r = _real()
    assert notify_real_execution(r, settings=_cfg(), reports_dir=tmp_path, post=spy)
    assert notify_real_execution(r, settings=_cfg(), reports_dir=tmp_path, post=spy) is False  # 중복
    assert len(spy.calls) == 1


# --- 전송 실패 흡수 ---
def test_http_post_swallows_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr("httpx.post", boom)
    assert dn._http_post("https://x", {"a": 1}) is False  # 예외 흡수 → False


def test_http_post_non_2xx_false(monkeypatch):
    class Resp:
        status_code = 500
    monkeypatch.setattr("httpx.post", lambda *a, **k: Resp())
    assert dn._http_post("https://x", {"a": 1}) is False


def test_failed_send_not_marked_sent(tmp_path):
    spy = Spy(ok=False)  # 전송 실패
    assert notify_real_execution(_real(), settings=_cfg(), reports_dir=tmp_path, post=spy) is False
    # 실패 시 dedupe 마킹 안 함 → 재시도 가능(파일 없음)
    assert not (tmp_path / "discord_sent.jsonl").exists()


# --- 시크릿/계정 미노출 ---
def test_no_secret_or_full_account_in_payload(tmp_path):
    import json
    spy = Spy()
    notify_real_execution(_real(), settings=_cfg(), reports_dir=tmp_path, post=spy)
    blob = json.dumps(spy.calls[0][1], ensure_ascii=False)
    assert "SECRET" not in blob and "discord.test" not in blob  # webhook URL 미포함
    assert "778689372" not in blob  # 전체 계정번호 미포함


# --- 알림은 주문을 내지 않는다(불변식) ---
def test_notify_does_not_place_orders(tmp_path):
    spy = Spy()
    notify_real_execution(_real(decision="REAL_READY_DRY_RUN"), settings=_cfg(), reports_dir=tmp_path, post=spy)
    emb = spy.calls[0][1]["embeds"][0]
    rop = [f for f in emb["fields"] if f["name"] == "real_orders_placed"][0]["value"]
    assert rop == "0"


# --- send_test (API) ---
def test_send_test_not_configured():
    assert send_test(settings=_nourl()) == {"configured": False, "sent": False}


def test_send_test_configured(monkeypatch):
    spy = Spy()
    assert send_test(settings=_cfg(), post=spy) == {"configured": True, "sent": True}
    assert len(spy.calls) == 1


def test_api_notify_test_not_configured(monkeypatch):
    # 내부 send_test()가 쓰는 Settings를 no-url로 강제(로컬 .env에 URL 있어도 무시 + 실제 전송 방지).
    monkeypatch.setattr(dn, "Settings", lambda: Settings(discord_webhook_url=None))
    body = TestClient(app).post("/api/notify/test").json()
    assert body == {"configured": False, "sent": False}


# --- append 통합: URL 있을 때 인라인 전송, 없을 때 무해 ---
def test_append_integration_inline_send(tmp_path, monkeypatch):
    from backend.app.services.real_order_executor import append_execution_receipt
    spy = Spy()
    monkeypatch.setattr(dn, "_http_post", spy)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    append_execution_receipt(_real(decision="REAL_READY_DRY_RUN"), reports_dir=tmp_path)
    assert len(spy.calls) == 1  # append가 인라인으로 알림 전송


def test_append_integration_noop_without_url(tmp_path, monkeypatch):
    from backend.app.services.position_manager import append_exit_decision
    spy = Spy()
    monkeypatch.setattr(dn, "_http_post", spy)
    # notify가 내부에서 쓰는 Settings를 no-url로 강제(로컬 .env에 URL 있어도 무시).
    monkeypatch.setattr(dn, "Settings", lambda: Settings(discord_webhook_url=None))
    d = append_exit_decision(_exit("STOP_LOSS"), reports_dir=tmp_path)
    assert d.exit_signal == "STOP_LOSS"  # append 정상
    assert spy.calls == []  # URL 없으면 미전송
