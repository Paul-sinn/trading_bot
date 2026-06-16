"""git hooks 테스트.

- 훅 파일(pre-commit, pre-push, install-hooks.sh) 존재 + 실행권한 확인.
- pre-commit: frontend 부재 시 graceful no-op(exit 0) 검증.
- pre-push: pytest를 호출하는 구조인지 정적 확인.
  (실제 pytest 재귀 실행은 하지 않는다 — 무한 재귀/타임아웃 방지.)
"""

import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT = PROJECT_ROOT / "scripts" / "git-hooks" / "pre-commit"
PRE_PUSH = PROJECT_ROOT / "scripts" / "git-hooks" / "pre-push"
INSTALL = PROJECT_ROOT / "scripts" / "install-hooks.sh"


def _is_executable(p: Path) -> bool:
    return p.exists() and bool(p.stat().st_mode & stat.S_IXUSR)


def test_hook_files_exist_and_executable():
    for p in (PRE_COMMIT, PRE_PUSH, INSTALL):
        assert p.exists(), f"{p} 없음"
        assert _is_executable(p), f"{p} 실행권한 없음"


def test_pre_commit_graceful_no_op_without_frontend():
    # frontend가 스캐폴드되지 않은 현재 상태 → lint/format 건너뛰고 exit 0.
    assert not (PROJECT_ROOT / "frontend" / "package.json").exists()
    result = subprocess.run(
        ["bash", str(PRE_COMMIT)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_pre_push_invokes_pytest_statically():
    # 정적 확인만 — 실제 실행 금지(무한 재귀/타임아웃 방지).
    content = PRE_PUSH.read_text()
    assert "pytest" in content


def test_install_script_copies_both_hooks_statically():
    content = INSTALL.read_text()
    assert "pre-commit" in content
    assert "pre-push" in content
    assert ".git/hooks" in content
