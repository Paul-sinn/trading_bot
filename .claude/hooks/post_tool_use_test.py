#!/usr/bin/env python3
"""PostToolUse hook — 파일 수정 후 대응하는 테스트 자동 실행.

Edit/Write로 `algorithms/`, `agents/`, `backend/` 하위 `.py`를 수정하면
대응하는 `tests/test_<stem>.py`가 있을 때만 그 테스트를 실행해 결과를 stdout에 출력한다.

CRITICAL (금지사항): PostToolUse는 절대 exit 2로 작업을 차단하지 않는다.
편집 도중 일시적 테스트 실패가 전체 작업을 막으면 안 된다. 항상 exit 0.
"""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WATCHED_PREFIXES = ("algorithms/", "agents/", "backend/")


def _extract_file_path(tool_input: object) -> str:
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("file_path", ""))


def _relative_to_root(file_path: str) -> str | None:
    """수정 파일을 프로젝트 루트 기준 상대경로로 정규화한다."""
    if not file_path:
        return None
    try:
        rel = Path(file_path).resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return rel.as_posix()


def _test_for(rel_path: str) -> Path | None:
    """수정 파일에 대응하는 테스트 파일 경로를 매핑한다.

    예: algorithms/signals.py → tests/test_signals.py
    """
    if not rel_path.endswith(".py"):
        return None
    if not rel_path.startswith(WATCHED_PREFIXES):
        return None
    stem = Path(rel_path).stem
    if stem == "__init__":
        return None
    test_path = PROJECT_ROOT / "tests" / f"test_{stem}.py"
    return test_path if test_path.exists() else None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0

    rel_path = _relative_to_root(_extract_file_path(payload.get("tool_input", {})))
    if rel_path is None:
        return 0

    test_path = _test_for(rel_path)
    if test_path is None:
        # 대응 테스트 없음 → 아무것도 하지 않는다.
        return 0

    rel_test = test_path.relative_to(PROJECT_ROOT).as_posix()
    print(f"[post_tool_use_test] {rel_path} 수정 → {rel_test} 실행")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", rel_test, "-q"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    # 테스트 실패해도 절대 차단하지 않는다.
    return 0


if __name__ == "__main__":
    sys.exit(main())
