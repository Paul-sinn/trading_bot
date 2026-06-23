"""Discord 승인 명령 처리 — `!approve`/`!reject`/`!status` 파싱·검증·결정 기록(순수 로직).

봇 워커(scripts/discord_approval_worker.py)가 이 함수를 호출한다. discord 라이브러리에 의존하지 않아
테스트 가능하다. **Robinhood를 절대 호출하지 않고 주문을 내지 않는다 — approval_decisions.jsonl만 쓴다.**

규칙:
- 허용 사용자 ID(DISCORD_ALLOWED_USER_IDS)만 승인/거부 가능. 그 외는 거부 + 감사 로그(valid=false).
- 만료된 요청은 승인 불가.
- 같은 요청에 이미 유효 결정이 있으면 중복 거부.
- 알 수 없는 approval_id 거부.
- !status는 결정을 쓰지 않는다(조회만).

spec: specs/real_order_v1_checklist.md §10
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.core.config import Settings
from backend.app.services.approval_gate import parse_allowed_user_ids
from backend.app.services.approval_store import (
    ApprovalDecision,
    append_decision,
    decisions_for,
    effective_status,
    get_request,
    to_view,
)
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


_COMMANDS = {"!approve", "!reject", "!status"}


def parse_command(text: str) -> tuple[str, str] | None:
    """`!approve <id>` 형태 파싱 → (command, approval_id). 형식 불량이면 None."""
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) < 2:
        return None
    cmd = parts[0].lower()
    if cmd not in _COMMANDS:
        return None
    return cmd, parts[1].strip()


def process_approval_command(
    *,
    text: str,
    discord_user_id: str,
    discord_username: str = "",
    channel_id: str = "",
    message_id: str = "",
    settings: Settings | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """명령을 처리하고 결과를 반환한다. approve/reject는 결정을 append, status는 조회만.

    반환: {"reply": str, "wrote_decision": bool, "valid": bool, "decision": str|None}.
    어떤 경우에도 주문/Robinhood 호출 없음.
    """
    settings = settings or Settings()
    now = now or _now()
    parsed = parse_command(text)
    if parsed is None:
        return {"reply": "사용법: !approve <id> | !reject <id> | !status <id>", "wrote_decision": False, "valid": False, "decision": None}
    cmd, approval_id = parsed

    req = get_request(approval_id, reports_dir=reports_dir)
    if req is None:
        # 알 수 없는 approval_id. approve/reject 시도는 감사 로그(valid=false)로 남긴다.
        if cmd in ("!approve", "!reject"):
            append_decision(
                ApprovalDecision(
                    approval_id=approval_id, decided_at=now.isoformat(),
                    decision="APPROVE" if cmd == "!approve" else "REJECT",
                    discord_user_id=discord_user_id, discord_username=discord_username,
                    channel_id=channel_id, message_id=message_id, raw_command=text,
                    valid=False, reason="알 수 없는 approval_id",
                ),
                reports_dir=reports_dir,
            )
            return {"reply": f"❌ 알 수 없는 approval_id: {approval_id}", "wrote_decision": True, "valid": False, "decision": None}
        return {"reply": f"❌ 알 수 없는 approval_id: {approval_id}", "wrote_decision": False, "valid": False, "decision": None}

    if cmd == "!status":
        v = to_view(req, reports_dir=reports_dir, now=now)
        return {"reply": f"ℹ️ {approval_id}: status={v.status} expired={v.expired} type={v.type} {v.symbol} {v.side}", "wrote_decision": False, "valid": True, "decision": None}

    decision = "APPROVE" if cmd == "!approve" else "REJECT"
    existing = decisions_for(approval_id, reports_dir=reports_dir)
    allowed = parse_allowed_user_ids(settings)

    # 허용 사용자 검증(목록이 비어있으면 누구도 허용 안 함 — fail-closed).
    if not allowed or discord_user_id not in allowed:
        append_decision(
            ApprovalDecision(
                approval_id=approval_id, decided_at=now.isoformat(), decision=decision,
                discord_user_id=discord_user_id, discord_username=discord_username,
                channel_id=channel_id, message_id=message_id, raw_command=text,
                valid=False, reason="허용되지 않은 Discord 사용자",
            ),
            reports_dir=reports_dir,
        )
        return {"reply": "❌ 권한 없음 — 승인/거부 허용 사용자가 아닙니다.", "wrote_decision": True, "valid": False, "decision": None}

    # 중복 결정 거부(이미 유효한 결정이 있으면).
    if any(d.valid for d in existing):
        append_decision(
            ApprovalDecision(
                approval_id=approval_id, decided_at=now.isoformat(), decision=decision,
                discord_user_id=discord_user_id, discord_username=discord_username,
                channel_id=channel_id, message_id=message_id, raw_command=text,
                valid=False, reason="이미 결정된 요청 (중복)",
            ),
            reports_dir=reports_dir,
        )
        return {"reply": f"❌ 이미 결정된 요청입니다: {approval_id}", "wrote_decision": True, "valid": False, "decision": None}

    # 만료 검증: 만료된 요청은 승인 불가(거부는 가능 — 안전 방향).
    status = effective_status(req, existing, now=now)
    if cmd == "!approve" and status == "EXPIRED":
        append_decision(
            ApprovalDecision(
                approval_id=approval_id, decided_at=now.isoformat(), decision="APPROVE",
                discord_user_id=discord_user_id, discord_username=discord_username,
                channel_id=channel_id, message_id=message_id, raw_command=text,
                valid=False, reason="만료된 요청은 승인 불가",
            ),
            reports_dir=reports_dir,
        )
        return {"reply": f"❌ 만료된 요청입니다: {approval_id}", "wrote_decision": True, "valid": False, "decision": None}

    append_decision(
        ApprovalDecision(
            approval_id=approval_id, decided_at=now.isoformat(), decision=decision,
            discord_user_id=discord_user_id, discord_username=discord_username,
            channel_id=channel_id, message_id=message_id, raw_command=text,
            valid=True, reason="ok",
        ),
        reports_dir=reports_dir,
    )
    icon = "✅" if decision == "APPROVE" else "🚫"
    return {"reply": f"{icon} {decision} 기록됨: {approval_id} (by {discord_username or discord_user_id})", "wrote_decision": True, "valid": True, "decision": decision}
