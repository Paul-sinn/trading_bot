"""Control flags — backend ↔ MCP 워커 사이의 안전 신호 파일.

backend(LiveSessionManager)가 start/stop/emergency-halt 시 `reports/control_flags.json`을
**덮어써서** 현재 제어 상태를 기록한다. Claude/Codex MCP 워커는 **어떤 액션(특히 주문) 전에도**
이 파일을 먼저 읽어 `block_new_orders`/`emergency_halt`를 확인해야 한다(fail-closed).

CRITICAL: 이 파일은 상태 신호일 뿐 주문을 내지 않는다. append가 아니라 현재 상태 1건만 유지한다.

spec: specs/live_session.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
CONTROL_FLAGS_FILE = "control_flags.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlFlags(BaseModel):
    """워커가 액션 전에 확인하는 제어 신호."""

    automation_running: bool = False
    emergency_halt: bool = False
    block_new_orders: bool = True
    block_new_llm_calls: bool = True
    updated_at: str = ""
    reason: str = ""


def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / CONTROL_FLAGS_FILE


def write_control_flags(flags: ControlFlags, *, reports_dir: Path | None = None) -> ControlFlags:
    """현재 제어 플래그를 덮어쓴다(updated_at 자동 갱신). 주문 없음 — 파일 쓰기만."""
    flags = flags.model_copy(update={"updated_at": _now_iso()})
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(flags.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return flags


def read_control_flags(*, reports_dir: Path | None = None) -> ControlFlags | None:
    """현재 제어 플래그를 읽는다. 부재/손상 시 None(워커는 None을 '차단'으로 fail-closed 처리해야 함)."""
    path = _path(reports_dir)
    if not path.exists():
        return None
    try:
        return ControlFlags.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, TypeError, OSError):
        return None
