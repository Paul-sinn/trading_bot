#!/usr/bin/env python3
"""PreToolUse hook — 주문 실행 전 리스크 체크 (kill-switch 게이트).

ADR-003: 코드 경로 어디서 주문이 나가든 단일 게이트를 통과하게 만든다.
주문 실행 패턴일 때만 `agents.risk.check_risk_gate()`를 호출해 한도를 평가하고,
차단 시 stderr에 사유를 출력하고 exit 2(Claude Code 툴 차단)로 종료한다.

CRITICAL (CLAUDE.md / ADR-003):
- 주문 패턴인데 리스크 평가가 예외를 던지면 fail-closed(차단)한다. fail-open 금지.
- 주문이 아닌 모든 툴 호출은 즉시 allow(exit 0). 그래야 일반 작업이 멈추지 않는다.
"""

import json
import re
import sys
from pathlib import Path

# 프로젝트 루트를 import path에 추가 (.claude/hooks/ → 루트는 2단계 위).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# 주문 실행 패턴: Bash 명령에 포함되는 MCP 주문 함수명, 또는 주문 실행 MCP 툴명.
_ORDER_COMMAND_PATTERN = re.compile(r"place_(equity|option)_order")
_ORDER_TOOL_PATTERN = re.compile(r"place_(equity|option)_order")


def _is_order_execution(tool_name: str, tool_input: dict) -> bool:
    """주문 실행 호출인지 판별한다."""
    # 1) MCP 주문 실행 툴이 직접 호출된 경우.
    if _ORDER_TOOL_PATTERN.search(tool_name or ""):
        return True
    # 2) Bash 명령 안에 주문 함수가 포함된 경우.
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))
    if _ORDER_COMMAND_PATTERN.search(command):
        return True
    return False


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # payload 파싱 불가. 주문 여부를 알 수 없으므로 일반 작업을 막지 않되,
        # 주문 패턴 문자열이 raw에 보이면 안전하게 차단한다.
        if _ORDER_COMMAND_PATTERN.search(raw):
            print("리스크 게이트: payload 파싱 실패 + 주문 패턴 감지 → fail-closed 차단.", file=sys.stderr)
            return 2
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    if not _is_order_execution(tool_name, tool_input):
        # 주문이 아닌 모든 툴 호출은 즉시 허용.
        return 0

    # 주문 실행 → 리스크 게이트 평가. 예외는 fail-closed로 처리.
    try:
        from agents.risk import check_risk_gate

        allowed, reason = check_risk_gate()
    except Exception as exc:  # noqa: BLE001 — 어떤 예외든 안전하게 차단.
        print(f"리스크 게이트 평가 실패 → fail-closed 차단: {exc}", file=sys.stderr)
        return 2

    if not allowed:
        print(f"리스크 게이트 차단: {reason}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
