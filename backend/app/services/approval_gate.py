"""Discord 승인 게이트 평가 — 실주문 제출 전 승인 상태를 검증하는 순수 게이트.

`evaluate_approval_gate`는 readiness 게이트(broker/risk/market-hours)와 **별개의 추가 전제조건**이다.
승인은 리스크 게이트를 우회하지 않는다 — 승인 + 모든 readiness + 확인까지 통과해야 제출 시도.

차단 조건(하나라도 위반 → not approved):
- REQUIRE_DISCORD_APPROVAL_FOR_REAL_ORDER=true인데 유효 승인 없음.
- approval_id에 해당하는 요청 없음/취소됨.
- 실효 상태가 APPROVED 아님(PENDING/REJECTED/EXPIRED/CANCELLED).
- 최근 유효 결정이 허용 Discord 사용자(allowed_user_ids)의 APPROVE 아님.
- 승인 시점 preview_hash ≠ 현재 주문 preview_hash(주문이 바뀜).
- idempotency_key가 이미 소비됨(중복 실행).
- notional > 캡 / 일일 실주문 한도 초과(belt-and-suspenders 재확인).

이 모듈은 Robinhood/주문을 호출하지 않는다. 로컬 상태만 읽어 판정한다.
spec: specs/real_order_v1_checklist.md §10
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.approval_store import (
    ApprovalDecision,
    ApprovalRequest,
    effective_status,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_allowed_user_ids(settings: Settings) -> set[str]:
    """콤마 구분 허용 사용자 ID 문자열 → 집합. 공백/빈 항목 제거."""
    return {x.strip() for x in (settings.discord_allowed_user_ids or "").split(",") if x.strip()}


class ApprovalGateResult(BaseModel):
    approved: bool
    block_reasons: list[str] = Field(default_factory=list)


def evaluate_approval_gate(
    *,
    settings: Settings,
    request: ApprovalRequest | None,
    decisions: list[ApprovalDecision],
    current_preview_hash: str,
    daily_real_count: int = 0,
    executed_keys: set[str] | None = None,
    now: datetime | None = None,
) -> ApprovalGateResult:
    """승인 게이트 판정(실 write 없음 — 로컬 상태만). 모든 위반을 수집한다."""
    now = now or _now()
    executed_keys = executed_keys or set()
    reasons: list[str] = []

    if not settings.require_discord_approval_for_real_order:
        return ApprovalGateResult(approved=True, block_reasons=[])

    if request is None:
        return ApprovalGateResult(approved=False, block_reasons=["Discord 승인 요청 없음"])

    status = effective_status(request, decisions, now=now)
    if status != "APPROVED":
        reasons.append(f"승인 상태 아님: {status}")

    allowed = parse_allowed_user_ids(settings)
    last_valid = next((d for d in reversed(decisions) if d.valid), None)
    if last_valid is None or last_valid.decision != "APPROVE":
        reasons.append("허용 사용자의 APPROVE 결정 없음")
    elif allowed and last_valid.discord_user_id not in allowed:
        reasons.append("승인자가 허용 사용자 목록에 없음")

    if request.preview_hash != current_preview_hash:
        reasons.append("preview_hash 불일치 (승인 후 주문 변경)")

    if request.idempotency_key in executed_keys:
        reasons.append("이미 실행됨 (idempotency)")

    # belt-and-suspenders: 승인 후에도 캡/일일 한도 재확인.
    if request.notional is not None and request.notional > settings.max_notional_per_real_order_usd:
        reasons.append(
            f"notional > MAX_NOTIONAL_PER_REAL_ORDER: {request.notional} > {settings.max_notional_per_real_order_usd}"
        )
    if daily_real_count >= settings.max_real_orders_per_day:
        reasons.append("MAX_REAL_ORDERS_PER_DAY 초과")

    return ApprovalGateResult(approved=not reasons, block_reasons=reasons)


def approval_gate_for_intent(
    intent,
    *,
    type: str,
    settings: Settings,
    account_last4: str | None,
    reports_dir=None,
    now: datetime | None = None,
    daily_real_count: int = 0,
    executed_keys: set[str] | None = None,
) -> ApprovalGateResult:
    """제출 시점 intent로 승인 게이트를 평가한다. 현재 주문으로 preview_hash를 재계산해 요청과 대조한다."""
    from backend.app.services.approval_store import (
        compute_preview_hash,
        decisions_for,
        get_request_for_intent,
    )

    current_hash = compute_preview_hash(
        type=type, symbol=intent.symbol, side=intent.side, order_type=intent.planned_order_type,
        quantity=intent.planned_quantity, limit_price=intent.planned_limit_price,
        notional=intent.planned_notional_usd, account_last4=account_last4,
        source_intent_id=intent.scan_event_key, strategy_id=intent.strategy_id,
        idempotency_key=intent.scan_event_key,
    )
    req = get_request_for_intent(intent.scan_event_key, reports_dir=reports_dir)
    decs = decisions_for(req.approval_id, reports_dir=reports_dir) if req else []
    return evaluate_approval_gate(
        settings=settings, request=req, decisions=decs, current_preview_hash=current_hash,
        daily_real_count=daily_real_count, executed_keys=executed_keys, now=now,
    )
