"""git hooks 테스트.

- 훅 파일(pre-commit, pre-push, install-hooks.sh) 존재 + 실행권한 확인.
- pre-commit: frontend 부재 시 graceful no-op(exit 0) 검증.
- pre-push: pytest를 호출하는 구조인지 정적 확인.
  (실제 pytest 재귀 실행은 하지 않는다 — 무한 재귀/타임아웃 방지.)
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT = PROJECT_ROOT / "scripts" / "git-hooks" / "pre-commit"
PRE_PUSH = PROJECT_ROOT / "scripts" / "git-hooks" / "pre-push"
INSTALL = PROJECT_ROOT / "scripts" / "install-hooks.sh"

# Git Bash 표준 설치 경로 — Windows에서 bash가 PATH에 없을 때 탐색한다.
_WINDOWS_BASH_FALLBACKS = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _find_bash() -> str | None:
    """PATH 우선, 없으면 Git Bash 표준 경로에서 bash를 찾는다. 못 찾으면 None."""
    found = shutil.which("bash")
    if found:
        return found
    for cand in _WINDOWS_BASH_FALLBACKS:
        if Path(cand).exists():
            return cand
    return None


def _git_index_mode(p: Path) -> str | None:
    """git 인덱스에 기록된 파일 모드(예: '100755'). 미추적/실패 시 None."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--stage", str(p)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.split()[0]


def _is_executable(p: Path) -> bool:
    """실행권한 확인 (크로스플랫폼).

    POSIX: 워킹트리 파일의 +x 비트(S_IXUSR).
    Windows: 워킹트리에 Unix 실행 비트가 없으므로 git 인덱스에 커밋된 모드가 755인지로
    검증한다(Unix 체크아웃 시 훅이 실행 가능함을 동등하게 보장 — 누가 644로 커밋하면 잡힘).
    """
    if not p.exists():
        return False
    if os.name == "nt":
        mode = _git_index_mode(p)
        return mode is not None and mode.endswith("755")
    return bool(p.stat().st_mode & stat.S_IXUSR)


def test_hook_files_exist_and_executable():
    for p in (PRE_COMMIT, PRE_PUSH, INSTALL):
        assert p.exists(), f"{p} 없음"
        assert _is_executable(p), f"{p} 실행권한 없음"


def test_pre_commit_graceful_no_op_without_frontend(tmp_path):
    # frontend가 없는 환경에서 pre-commit은 lint/format을 건너뛰고 exit 0 한다.
    # repo 실제 상태(이제 frontend가 스캐폴드됨)에 의존하지 않도록, frontend가 없는
    # 격리된 임시 디렉토리에서 hook을 실행해 no-op 분기를 검증한다.
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash 미설치 — POSIX 훅 실행 검증 스킵(Windows에 Git Bash 없음)")
    assert not (tmp_path / "frontend").exists()
    result = subprocess.run(
        [bash, str(PRE_COMMIT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",  # 훅 출력의 한글("건너뜀")을 locale(cp949) 아닌 UTF-8로 디코드.
    )
    assert result.returncode == 0, result.stderr
    assert "건너뜀" in result.stdout


def test_pre_push_invokes_pytest_statically():
    # 정적 확인만 — 실제 실행 금지(무한 재귀/타임아웃 방지).
    content = PRE_PUSH.read_text(encoding="utf-8")
    assert "pytest" in content


def test_install_script_copies_both_hooks_statically():
    content = INSTALL.read_text(encoding="utf-8")
    assert "pre-commit" in content
    assert "pre-push" in content
    assert ".git/hooks" in content
