"""Discord 승인 게이트 저장소 — 실주문(매수/매도) 전 사람이 Discord에서 승인해야 하는 안전 게이트.

append-only jsonl 두 파일(gitignore):
- `reports/approval_requests.jsonl` : 승인 요청(READY 도달 시 생성·Discord 전송).
- `reports/approval_decisions.jsonl`: Discord 봇 워커가 쓴 승인/거부 결정(`!approve`/`!reject`).

CRITICAL 불변식:
- 승인은 **리스크 게이트를 우회하지 않는다**. 승인 + 모든 readiness 게이트 + 확인까지 통과해야 제출 시도.
- 시크릿/전체 계좌번호/토큰을 절대 기록하지 않는다(계좌는 last4만, discord_user_id만).
- 이 모듈은 Robinhood/주문을 호출하지 않는다. 파일 읽기/쓰기 + Discord 메시지(webhook)만.
- 테스트성/수동 intent는 승인 요청 자체가 생성되지 않는다(strategy/live-scan intent만).

spec: specs/real_order_v1_checklist.md §10
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot
from backend.app.services.execution_gate import OrderIntent

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
REQUESTS_LOG = "approval_requests.jsonl"
DECISIONS_LOG = "approval_decisions.jsonl"

ApprovalType = Literal["BUY", "SELL"]
ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "CANCELLED"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class ApprovalRequest(BaseModel):
    """실주문 승인 요청. 생성만으로는 주문이 아니다(broker_order_id 항상 null)."""

    approval_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    created_at: str = Field(default_factory=_now_iso)
    expires_at: str = ""
    type: ApprovalType
    symbol: str
    side: str
    order_type: str = "limit"
    quantity: float | None = None
    dollar_amount: float | None = None
    limit_price: float | None = None
    notional: float | None = None
    account_last4: str | None = None
    source_intent_id: str
    strategy_id: str
    idempotency_key: str
    preview_hash: str
    status: ApprovalStatus = "PENDING"
    reason: str = ""
    broker_order_id: None = None


class ApprovalDecision(BaseModel):
    """Discord 승인/거부 결정. 봇 워커만 쓴다. 시크릿/토큰 미포함(discord_user_id만)."""

    approval_id: str
    decided_at: str = Field(default_factory=_now_iso)
    decision: Literal["APPROVE", "REJECT"]
    discord_user_id: str = ""
    discord_username: str = ""
    channel_id: str = ""
    message_id: str = ""
    raw_command: str = ""
    valid: bool = True
    reason: str = ""


# --- preview hash (승인 시점 ↔ 제출 시점 주문 일치 보장) ---
def compute_preview_hash(
    *,
    type: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None,
    limit_price: float | None,
    notional: float | None,
    account_last4: str | None,
    source_intent_id: str,
    strategy_id: str,
    idempotency_key: str,
) -> str:
    """주문 핵심 필드의 정준 해시(sha256). 승인 후 주문이 바뀌면 해시 불일치로 차단된다."""
    payload = json.dumps(
        {
            "type": type, "symbol": symbol, "side": side, "order_type": order_type,
            "quantity": quantity, "limit_price": limit_price, "notional": notional,
            "account_last4": account_last4, "source_intent_id": source_intent_id,
            "strategy_id": strategy_id, "idempotency_key": idempotency_key,
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- 파일 IO ---
def _req_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / REQUESTS_LOG


def _dec_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / DECISIONS_LOG


def append_request(req: ApprovalRequest, *, reports_dir: Path | None = None) -> ApprovalRequest:
    path = _req_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(req.model_dump(), ensure_ascii=False) + "\n")
    return req


def append_decision(dec: ApprovalDecision, *, reports_dir: Path | None = None) -> ApprovalDecision:
    path = _dec_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dec.model_dump(), ensure_ascii=False) + "\n")
    return dec


def _load(path: Path, model: type[BaseModel]) -> list:
    if not path.exists():
        return []
    out: list = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(model.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out


def load_requests(*, limit: int = 50, reports_dir: Path | None = None) -> list[ApprovalRequest]:
    limit = max(1, min(int(limit), 500))
    return _load(_req_path(reports_dir), ApprovalRequest)[-limit:]


def load_decisions(*, limit: int = 500, reports_dir: Path | None = None) -> list[ApprovalDecision]:
    limit = max(1, min(int(limit), 2000))
    return _load(_dec_path(reports_dir), ApprovalDecision)[-limit:]


def get_request(approval_id: str, *, reports_dir: Path | None = None) -> ApprovalRequest | None:
    """approval_id의 가장 최근 요청(중복 생성 방지 — 마지막 것)."""
    matches = [r for r in load_requests(limit=500, reports_dir=reports_dir) if r.approval_id == approval_id]
    return matches[-1] if matches else None


def get_request_for_intent(source_intent_id: str, *, reports_dir: Path | None = None) -> ApprovalRequest | None:
    """source_intent_id(=scan_event_key)의 가장 최근 승인 요청. 제출 시점에 intent로 요청을 찾는다."""
    matches = [r for r in load_requests(limit=500, reports_dir=reports_dir) if r.source_intent_id == source_intent_id]
    return matches[-1] if matches else None


def decisions_for(approval_id: str, *, reports_dir: Path | None = None) -> list[ApprovalDecision]:
    return [d for d in load_decisions(reports_dir=reports_dir) if d.approval_id == approval_id]


def latest_decision_for(approval_id: str, *, reports_dir: Path | None = None) -> ApprovalDecision | None:
    ds = decisions_for(approval_id, reports_dir=reports_dir)
    return ds[-1] if ds else None


def _is_expired(req: ApprovalRequest, *, now: datetime | None = None) -> bool:
    try:
        exp = datetime.fromisoformat(req.expires_at)
    except (ValueError, TypeError):
        return True  # 파싱 불가 → 만료로 간주(fail-closed)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (now or _now()) >= exp


def effective_status(
    req: ApprovalRequest, decisions: list[ApprovalDecision], *, now: datetime | None = None
) -> ApprovalStatus:
    """요청 + 결정 + 만료로 실효 상태를 도출한다. **유효한 첫 결정**이 상태를 확정한다.

    CANCELLED는 요청에 명시된 경우만. 그 외엔 결정(유효)이 있으면 APPROVED/REJECTED,
    없으면 만료 여부에 따라 EXPIRED/PENDING.
    """
    if req.status == "CANCELLED":
        return "CANCELLED"
    for d in decisions:  # 시간순 — 유효한 첫 결정이 확정
        if not d.valid:
            continue
        if d.decision == "APPROVE":
            # 만료 후의 승인은 인정하지 않는다(만료 우선).
            return "EXPIRED" if _is_expired(req, now=now) else "APPROVED"
        if d.decision == "REJECT":
            return "REJECTED"
    return "EXPIRED" if _is_expired(req, now=now) else "PENDING"


class ApprovalView(BaseModel):
    """API/대시보드용 요청 + 실효 상태 + 최근 결정 요약(읽기 전용)."""

    approval_id: str
    created_at: str
    expires_at: str
    type: str
    symbol: str
    side: str
    order_type: str
    quantity: float | None = None
    dollar_amount: float | None = None
    limit_price: float | None = None
    notional: float | None = None
    account_last4: str | None = None
    strategy_id: str
    status: ApprovalStatus
    expired: bool
    reason: str = ""
    approve_command: str
    reject_command: str
    decided_by: str | None = None
    decision: str | None = None


def to_view(req: ApprovalRequest, *, reports_dir: Path | None = None, now: datetime | None = None) -> ApprovalView:
    ds = decisions_for(req.approval_id, reports_dir=reports_dir)
    status = effective_status(req, ds, now=now)
    last = next((d for d in reversed(ds) if d.valid), None)
    return ApprovalView(
        approval_id=req.approval_id, created_at=req.created_at, expires_at=req.expires_at,
        type=req.type, symbol=req.symbol, side=req.side, order_type=req.order_type,
        quantity=req.quantity, dollar_amount=req.dollar_amount, limit_price=req.limit_price,
        notional=req.notional, account_last4=req.account_last4, strategy_id=req.strategy_id,
        status=status, expired=_is_expired(req, now=now), reason=req.reason,
        approve_command=f"!approve {req.approval_id}", reject_command=f"!reject {req.approval_id}",
        decided_by=(last.discord_username or last.discord_user_id) if last else None,
        decision=last.decision if last else None,
    )


# --- 승인 요청 생성(READY 도달 시) ---
class ApprovalRequestRefused(ValueError):
    """승인 요청 생성 거부 — 전략 intent 아님/캡 초과 등(실주문 후보 부적격)."""


def _is_strategy_intent(intent: OrderIntent, settings: Settings) -> bool:
    return intent.strategy_id == settings.live_strategy_id


def create_approval_request(
    intent: OrderIntent,
    *,
    type: ApprovalType,
    settings: Settings,
    snapshot: BrokerSnapshot | None,
    now: datetime | None = None,
    reports_dir: Path | None = None,
    post=None,
    send: bool = True,
) -> ApprovalRequest:
    """READY 상태 실주문 intent로 승인 요청을 만들어 append + Discord 전송한다(주문 아님).

    전략/라이브스캔 생성 intent만 허용(테스트성/수동 intent는 ApprovalRequestRefused). notional 캡 초과도 거부.
    """
    now = now or _now()
    # 출처 게이트: 테스트성 intent는 승인 요청조차 만들지 않는다(설정으로만 예외 허용).
    if settings.strategy_intent_only_for_real_order and not _is_strategy_intent(intent, settings):
        if not settings.test_only_intent_real_order_allowed:
            raise ApprovalRequestRefused("test-only/manual intent — 실주문 승인 요청 불가 (strategy intent only)")
    notional = intent.planned_notional_usd
    if notional is not None and notional > settings.max_notional_per_real_order_usd:
        raise ApprovalRequestRefused(
            f"notional > MAX_NOTIONAL_PER_REAL_ORDER: {notional} > {settings.max_notional_per_real_order_usd}"
        )
    account_last4 = snapshot.account_last4 if snapshot is not None else None
    preview_hash = compute_preview_hash(
        type=type, symbol=intent.symbol, side=intent.side, order_type=intent.planned_order_type,
        quantity=intent.planned_quantity, limit_price=intent.planned_limit_price, notional=notional,
        account_last4=account_last4, source_intent_id=intent.scan_event_key,
        strategy_id=intent.strategy_id, idempotency_key=intent.scan_event_key,
    )
    req = ApprovalRequest(
        expires_at=(now + timedelta(seconds=settings.approval_request_ttl_seconds)).isoformat(),
        type=type, symbol=intent.symbol, side=intent.side, order_type=intent.planned_order_type,
        quantity=intent.planned_quantity, dollar_amount=notional, limit_price=intent.planned_limit_price,
        notional=notional, account_last4=account_last4, source_intent_id=intent.scan_event_key,
        strategy_id=intent.strategy_id, idempotency_key=intent.scan_event_key, preview_hash=preview_hash,
    )
    append_request(req, reports_dir=reports_dir)
    if send:
        try:  # 전송 실패가 요청 기록/흐름을 죽이지 않게 흡수. URL 없으면 no-op.
            from backend.app.services.discord_notifier import notify_approval_request

            notify_approval_request(req, settings=settings, reports_dir=reports_dir, post=post)
        except Exception:  # noqa: BLE001
            pass
    return req
