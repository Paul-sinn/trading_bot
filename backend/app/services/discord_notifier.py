"""Discord 알림 — 매매 이벤트를 webhook으로 전송(backend 전용).

CRITICAL(ADR-001): 외부 HTTP(Discord webhook) + 시크릿은 backend service에만 격리한다.
- `DISCORD_WEBHOOK_URL`은 `.env`에서만 읽고 **로그/커밋/페이로드에 노출하지 않는다.**
- URL 없거나 카테고리 토글 off면 **no-op**(안전 기본값). 전송 실패는 흡수 — 매매/파이프라인을
  절대 죽이지 않는다.
- **알림은 메시지만 보낸다. 주문/매도/취소를 하지 않는다.** report_only 불변식과 무관(안전).
- 같은 이벤트 id는 한 번만 전송한다(dedupe — `reports/discord_sent.jsonl`, gitignore).
- 계정번호는 last4만(스냅샷 단계에서 이미 마스킹). 시크릿 미포함.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from backend.app.core.config import Settings

if TYPE_CHECKING:  # 런타임 import 안 함(순환참조 방지) — 속성만 덕타이핑으로 읽는다.
    from backend.app.services.approval_store import ApprovalRequest
    from backend.app.services.order_receipt import OrderReceipt
    from backend.app.services.position_manager import ExitDecision
    from backend.app.services.real_order_executor import RealExecutionReceipt

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
SENT_LOG = "discord_sent.jsonl"

# Discord embed 색상(10진).
GREEN = 0x2ECC71
RED = 0xE74C3C
AMBER = 0xF39C12
BLUE = 0x3498DB
GREY = 0x95A5A6

# 전송 함수 시그니처: (url, payload) -> 성공 여부. 테스트는 이걸 주입/몽키패치한다.
PostFn = Callable[[str, dict], bool]


def _http_post(url: str, payload: dict, timeout: float = 5.0) -> bool:
    """webhook으로 POST. 2xx면 True. 어떤 예외도 흡수(False) — 매매 흐름을 막지 않는다."""
    try:
        import httpx

        resp = httpx.post(url, json=payload, timeout=timeout)
        return 200 <= resp.status_code < 300
    except Exception:  # noqa: BLE001 - 알림 실패가 매매를 죽이지 않게(graceful)
        return False


# --- dedupe ---
def _sent_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / SENT_LOG


def _already_sent(event_id: str, reports_dir: Path | None) -> bool:
    path = _sent_path(reports_dir)
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if json.loads(line).get("event_id") == event_id:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _mark_sent(event_id: str, reports_dir: Path | None) -> None:
    path = _sent_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_id": event_id}, ensure_ascii=False) + "\n")


def _dispatch(
    event_id: str,
    embed: dict,
    *,
    settings: Settings,
    reports_dir: Path | None,
    post: PostFn | None = None,
    dedupe: bool = True,
) -> bool:
    """URL 있으면 dedupe 후 전송. URL 없으면 즉시 no-op(파일도 안 건드림)."""
    url = settings.discord_webhook_url
    if not url:
        return False  # 비활성(안전 기본값)
    if dedupe and _already_sent(event_id, reports_dir):
        return False
    ok = (post or _http_post)(url, {"embeds": [embed]})
    if ok and dedupe:
        _mark_sent(event_id, reports_dir)
    return ok


def _field(name: str, value, inline: bool = True) -> dict:
    return {"name": name, "value": "—" if value is None else str(value), "inline": inline}


# --- embed 빌더(순수 함수 — 시크릿/전체 계정번호 미포함) ---
def build_real_execution_embed(r: RealExecutionReceipt) -> dict:
    decision = r.decision
    if decision == "REAL_SUBMITTED":
        color = RED if r.side == "SELL" else GREEN
        title = f"{'🔴 실매도' if r.side == 'SELL' else '🟢 실매수'} 체결 (REAL_SUBMITTED)"
    elif decision == "MOCK_SUBMITTED":
        color, title = GREY, "⚪ MOCK 제출 (테스트)"
    elif decision == "REAL_READY_DRY_RUN":
        color, title = BLUE, "🔵 실행 준비 (REAL_READY_DRY_RUN)"
    else:  # REAL_BLOCKED / 기타
        color, title = AMBER, f"🟡 실행 차단 ({decision})"
    return {
        "title": title,
        "color": color,
        "fields": [
            _field("종목", r.symbol),
            _field("방향", r.side),
            _field("지정가", r.limit_price),
            _field("노셔널", f"${r.notional}" if r.notional is not None else None),
            _field("수량", r.quantity),
            _field("환경", f"{r.environment}/{r.market_hours_source}"),
            _field("broker_order_id", r.broker_order_id),
            _field("real_orders_placed", r.real_orders_placed),
            _field("사유", r.reason, inline=False),
        ],
        "footer": {"text": f"is_proof_run={r.is_proof_run} · {r.timestamp}"},
    }


def build_exit_embed(d: ExitDecision) -> dict:
    sig = d.exit_signal
    color = RED if sig in ("STOP_LOSS", "TRAILING_STOP", "TIME_STOP") else BLUE
    icon = "🔴" if color == RED else "🔵"
    pct = None if d.unrealized_pnl_pct is None else f"{d.unrealized_pnl_pct * 100:.2f}%"
    return {
        "title": f"{icon} 청산 신호 ({sig})",
        "color": color,
        "fields": [
            _field("종목", d.symbol),
            _field("수량", d.quantity),
            _field("평단", d.average_buy_price),
            _field("현재가", d.current_price),
            _field("미실현 손익%", pct),
            _field("would_sell", d.would_sell_quantity),
            _field("real_orders_placed", d.real_orders_placed),
            _field("사유", d.reason, inline=False),
        ],
        "footer": {"text": f"dry-run only — no sell order submitted · {d.timestamp}"},
    }


def build_order_receipt_embed(r: OrderReceipt) -> dict:
    if r.decision == "WOULD_SUBMIT":
        color, title = GREEN, "🟢 dry-run 주문계획 (WOULD_SUBMIT)"
    elif r.decision == "ERROR":
        color, title = RED, "🔴 주문 영수증 ERROR"
    else:  # BLOCKED / SKIPPED
        color, title = AMBER, f"🟡 dry-run 영수증 ({r.decision})"
    return {
        "title": title,
        "color": color,
        "fields": [
            _field("종목", r.symbol),
            _field("방향", r.side),
            _field("지정가", r.limit_price),
            _field("노셔널", f"${r.notional}" if r.notional is not None else None),
            _field("broker_order_id", r.broker_order_id),
            _field("real_orders_placed", r.real_orders_placed),
            _field("사유", r.reason, inline=False),
        ],
        "footer": {"text": f"dry-run receipt only — no real order · {r.timestamp}"},
    }


# --- append 지점에서 호출하는 디스패처(안전: URL 없으면 no-op, 예외 흡수) ---
def notify_real_execution(
    receipt: RealExecutionReceipt, *, settings: Settings | None = None, reports_dir: Path | None = None,
    post: PostFn | None = None,
) -> bool:
    settings = settings or Settings()
    if not settings.discord_webhook_url:
        return False
    is_block = receipt.decision in ("REAL_BLOCKED",) or receipt.decision == "ERROR"
    allowed = settings.discord_notify_blocks if is_block else settings.discord_notify_real_orders
    if not allowed:
        return False
    return _dispatch(
        f"realexec:{receipt.receipt_id}", build_real_execution_embed(receipt),
        settings=settings, reports_dir=reports_dir, post=post,
    )


def notify_exit(
    decision: ExitDecision, *, settings: Settings | None = None, reports_dir: Path | None = None,
    post: PostFn | None = None,
) -> bool:
    settings = settings or Settings()
    if not settings.discord_webhook_url or not settings.discord_notify_exits:
        return False
    if decision.exit_signal == "HOLD":
        return False  # HOLD는 노이즈 — 알리지 않음
    event_id = f"exit:{decision.symbol}:{decision.timestamp}:{decision.exit_signal}"
    return _dispatch(
        event_id, build_exit_embed(decision),
        settings=settings, reports_dir=reports_dir, post=post,
    )


def notify_order_receipt(
    receipt: OrderReceipt, *, settings: Settings | None = None, reports_dir: Path | None = None,
    post: PostFn | None = None,
) -> bool:
    settings = settings or Settings()
    if not settings.discord_webhook_url:
        return False
    if receipt.decision == "WOULD_SUBMIT":
        if not settings.discord_notify_dry_run_intents:
            return False
    elif not settings.discord_notify_blocks:  # BLOCKED / ERROR / SKIPPED
        return False
    return _dispatch(
        f"orderreceipt:{receipt.receipt_id}", build_order_receipt_embed(receipt),
        settings=settings, reports_dir=reports_dir, post=post,
    )


def build_approval_request_embed(req: ApprovalRequest) -> dict:
    """승인 요청 embed — 승인/거부 명령 포함. 시크릿/전체 계좌번호 미포함(last4만)."""
    color = RED if req.side == "SELL" else GREEN
    icon = "🔴" if req.side == "SELL" else "🟢"
    fields = [
        _field("종목", req.symbol),
        _field("방향", req.side),
        _field("주문유형", req.order_type),
        _field("지정가", req.limit_price),
        _field("노셔널", f"${req.notional}" if req.notional is not None else None),
        _field("수량", req.quantity if req.quantity is not None else (f"${req.dollar_amount}" if req.dollar_amount is not None else None)),
        _field("호가(bid/ask/last)", f"{req.bid}/{req.ask}/{req.last}" if (req.bid or req.ask or req.last) else None),
        _field("스프레드%", f"{req.spread_pct * 100:.3f}%" if req.spread_pct is not None else None),
        _field("계좌", req.account_last4),
        _field("전략", req.strategy_id),
        _field("만료", req.expires_at),
        _field("approval_id", req.approval_id, inline=False),
        _field("승인", f"`!approve {req.approval_id}`"),
        _field("거부", f"`!reject {req.approval_id}`"),
    ]
    return {
        "title": f"{icon} 실주문 승인 요청 ({req.type})",
        "color": color,
        "description": "Discord 승인이 있어야 실주문이 진행됩니다. 승인은 리스크 게이트를 우회하지 않습니다.",
        "fields": fields,
        "footer": {"text": f"PENDING — 만료 전 !approve/!reject · {req.created_at}"},
    }


def notify_approval_request(
    request: ApprovalRequest, *, settings: Settings | None = None, reports_dir: Path | None = None,
    post: PostFn | None = None,
) -> bool:
    """승인 요청을 Discord로 전송(webhook). URL 없으면 no-op. 주문 없음."""
    settings = settings or Settings()
    if not settings.discord_webhook_url:
        return False
    return _dispatch(
        f"approval:{request.approval_id}", build_approval_request_embed(request),
        settings=settings, reports_dir=reports_dir, post=post,
    )


def send_test(*, settings: Settings | None = None, post: PostFn | None = None) -> dict:
    """수동 테스트 핑(연결 확인용). dedupe 없이 1회 전송. 주문 없음."""
    settings = settings or Settings()
    configured = bool(settings.discord_webhook_url)
    if not configured:
        return {"configured": False, "sent": False}
    embed = {
        "title": "✅ 트레이딩 봇 알림 연결 테스트",
        "color": BLUE,
        "description": "Discord 알림이 정상 연결되었습니다. (실주문 없음 — 테스트 메시지)",
    }
    sent = _dispatch("test-ping", embed, settings=settings, reports_dir=None, post=post, dedupe=False)
    return {"configured": True, "sent": sent}
