"""Claude Code hooks 테스트.

스크립트를 subprocess로 실제 실행해 exit code와 차단 동작을 검증한다.
- pre_tool_use_risk: 주문 패턴 + kill-switch on → exit 2, off → exit 0, 비주문 → exit 0.
- post_tool_use_test: 매핑되는 테스트 없는 파일 → exit 0, 차단 없음.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRE_HOOK = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use_risk.py"
POST_HOOK = PROJECT_ROOT / ".claude" / "hooks" / "post_tool_use_test.py"

ORDER_PAYLOAD = {
    "tool_name": "Bash",
    "tool_input": {"command": "place_equity_order AAPL 10"},
}
NON_ORDER_PAYLOAD = {"tool_name": "Read", "tool_input": {}}


def _run_hook(hook: Path, payload: dict, env_extra: dict | None = None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def test_pre_hook_blocks_order_when_kill_switch_on():
    result = _run_hook(PRE_HOOK, ORDER_PAYLOAD, {"RISK_KILL_SWITCH": "on"})
    assert result.returncode == 2
    assert result.stderr.strip() != ""


def test_pre_hook_allows_order_when_kill_switch_off():
    result = _run_hook(PRE_HOOK, ORDER_PAYLOAD, {"RISK_KILL_SWITCH": "off"})
    assert result.returncode == 0


def test_pre_hook_allows_non_order_even_with_kill_switch_on():
    # 비주문 툴은 kill-switch 상태와 무관하게 항상 허용된다.
    result = _run_hook(PRE_HOOK, NON_ORDER_PAYLOAD, {"RISK_KILL_SWITCH": "on"})
    assert result.returncode == 0


def test_pre_hook_blocks_mcp_order_tool_when_kill_switch_on():
    payload = {"tool_name": "mcp__robinhood__place_equity_order", "tool_input": {}}
    result = _run_hook(PRE_HOOK, payload, {"RISK_KILL_SWITCH": "on"})
    assert result.returncode == 2


def test_post_hook_no_op_when_no_matching_test():
    # tests/test_config.py가 없으므로 차단 없이 exit 0.
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(PROJECT_ROOT / "backend" / "app" / "core" / "config.py")},
    }
    result = _run_hook(POST_HOOK, payload)
    assert result.returncode == 0


def test_post_hook_no_op_for_unwatched_file():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(PROJECT_ROOT / "README.md")},
    }
    result = _run_hook(POST_HOOK, payload)
    assert result.returncode == 0
