"""수동 arm 계약 — 실주문 실행 전 사람이 명시적으로 무장(arm)해야 하는 안전 게이트.

`reports/real_order_arm.json`(gitignore)에 수동으로 만든 arm 파일이 있고, armed=true이며 만료되지
않았을 때만 실행 readiness가 통과한다. 파일이 **없거나/만료/손상/armed=false면 실행은 BLOCKED**.

CRITICAL: 이 파일은 신호일 뿐 주문을 내지 않는다. arm이 통과해도 현재 단계엔 실 MCP 주문 경로가
없다(RealExecutionDisabled). 검증·greenlight 전 라이브 금지(헌장 §3/§10).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
ARM_FILE = "real_order_arm.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RealOrderArm(BaseModel):
    """실주문 무장 신호. expires_at 이후엔 무효."""

    armed: bool = False
    armed_at: str = ""
    expires_at: str = ""
    max_notional: float | None = None
    allowed_symbol: str | None = None
    reason: str = ""
    created_by: str = ""


def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / ARM_FILE


def read_arm(*, reports_dir: Path | None = None) -> RealOrderArm | None:
    """arm 파일을 읽는다. 부재/손상 → None(호출부는 None을 '차단'으로 fail-closed 처리)."""
    path = _path(reports_dir)
    if not path.exists():
        return None
    try:
        return RealOrderArm.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, TypeError, OSError):
        return None


def arm_state(arm: RealOrderArm | None, *, now: datetime | None = None) -> str:
    """arm 상태 라벨: missing / disarmed / expired / armed."""
    if arm is None:
        return "missing"
    if not arm.armed:
        return "disarmed"
    if _is_expired(arm, now=now):
        return "expired"
    return "armed"


def is_armed(arm: RealOrderArm | None, *, now: datetime | None = None) -> bool:
    """실행 가능한 유효 arm인지(있고 armed=true이며 만료 전). fail-closed."""
    return arm_state(arm, now=now) == "armed"


def _is_expired(arm: RealOrderArm, *, now: datetime | None = None) -> bool:
    try:
        exp = datetime.fromisoformat(arm.expires_at)
    except (ValueError, TypeError):
        return True  # 파싱 불가 → 만료로 간주(fail-closed)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (now or _now()) >= exp


def write_arm(arm: RealOrderArm, *, reports_dir: Path | None = None) -> RealOrderArm:
    """arm 파일을 쓴다(수동/운영·테스트용). **주문을 내지 않는다 — 파일 쓰기만.**

    자동 경로에서 호출하지 말 것(수동 무장 전용).
    """
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(arm.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return arm
