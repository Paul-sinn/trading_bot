#!/usr/bin/env bash
# scripts/git-hooks/ 의 훅들을 .git/hooks/ 로 설치한다.
# .git/hooks 는 버전 관리되지 않으므로 훅 본체는 scripts/git-hooks/ 에 두고
# 이 스크립트로 배포한다. 여러 번 실행해도 안전(멱등)하다.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SRC_DIR="$REPO_ROOT/scripts/git-hooks"
DEST_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$DEST_DIR" ]; then
  echo "[install-hooks] .git/hooks 디렉토리가 없습니다. git 저장소 루트에서 실행하세요." >&2
  exit 1
fi

for hook in pre-commit pre-push; do
  src="$SRC_DIR/$hook"
  dest="$DEST_DIR/$hook"
  if [ ! -f "$src" ]; then
    echo "[install-hooks] $src 없음 — 건너뜀" >&2
    continue
  fi
  cp "$src" "$dest"
  chmod +x "$dest"
  echo "[install-hooks] 설치: .git/hooks/$hook"
done

echo "[install-hooks] 완료."
