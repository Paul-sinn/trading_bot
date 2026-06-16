# Step 2: git-hooks

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md` (개발 프로세스 — git hooks 규칙)
- `/docs/ADR.md` (ADR-006: SDD→TDD 강제)
- `/requirements.txt`, `/pyproject.toml` 또는 `/pytest.ini` (step 0 산출물)

## 작업

git hooks를 설정한다. 저장소 루트에 설치 스크립트를 두고, hook 본체는 버전 관리되는 `scripts/git-hooks/`에 둔다 (`.git/hooks`는 커밋되지 않으므로).

### 1. `scripts/git-hooks/pre-commit`

- ESLint + Prettier 자동 수정을 시도한다.
- **graceful no-op**: `frontend/package.json`이 없거나 `node_modules`가 없으면 lint/format을 건너뛰고 exit 0. 이유: 이 phase에는 아직 Next.js가 스캐폴드되지 않았다.
- frontend가 존재할 때만 `npm run lint --prefix frontend` / prettier를 실행하고, 실패 시 **exit 1**(커밋 차단).
- Python 파일에 대해서는 (선택) 간단한 `python -m py_compile` 구문 체크만. 무거운 포매터는 강제하지 않는다.

### 2. `scripts/git-hooks/pre-push`

- `pytest`를 실행해 단위테스트 전체 통과를 확인한다.
- 실패 시 **exit 1**(push 차단).
- `pytest`가 설치 안 된 환경이면 명확한 에러 메시지 출력 후 exit 1 (조용히 통과시키지 마라).

### 3. `scripts/install-hooks.sh`

- `scripts/git-hooks/`의 훅들을 `.git/hooks/`로 복사(또는 symlink)하고 실행권한(`chmod +x`)을 부여한다.
- 멱등성: 여러 번 실행해도 안전해야 한다.

### 4. 테스트

`tests/test_git_hooks.py`:
- `scripts/git-hooks/pre-commit`, `pre-push`, `scripts/install-hooks.sh` 파일이 존재하고 실행권한이 있는지 확인.
- pre-commit을 frontend 없는 상태에서 실행 → exit 0 (graceful no-op) 검증. (subprocess로 실행, 임시 빈 디렉토리 컨텍스트 또는 frontend 부재 분기 확인)
- pre-push 스크립트가 `pytest`를 호출하는 구조인지 정적 확인(스크립트 내용에 pytest 포함) — 실제 pytest 재귀 실행은 하지 마라(무한루프/시간초과 방지).

## Acceptance Criteria

```bash
bash scripts/install-hooks.sh
test -x .git/hooks/pre-commit && echo "pre-commit installed"
test -x .git/hooks/pre-push && echo "pre-push installed"
bash scripts/git-hooks/pre-commit; test $? -eq 0 && echo "pre-commit no-op OK"
pytest tests/test_git_hooks.py -v
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트:
   - CLAUDE.md의 git hooks 규칙(pre-commit lint, pre-push pytest)을 따르는가?
   - hook 본체가 버전 관리되는 위치(`scripts/git-hooks/`)에 있는가?
3. `phases/0-foundation/index.json`의 step 2를 업데이트한다:
   - 성공 → `"completed"` + `"summary"`
   - 실패 → `"error"` + `"error_message"`
   - 개입 필요 → `"blocked"` + `"blocked_reason"`

## 금지사항

- pre-push 테스트 안에서 `tests/test_git_hooks.py`가 다시 pytest 전체를 호출하게 만들지 마라. 이유: 무한 재귀/타임아웃. 정적 검증만 하라.
- frontend가 없는데 pre-commit이 npm 명령으로 실패해 커밋을 막게 하지 마라. 이유: 이 phase에서 모든 커밋이 차단된다. graceful no-op 필수.
- `.git/hooks/` 안의 훅을 직접 커밋하려 하지 마라. 이유: git이 추적하지 않는다. `scripts/git-hooks/`에 두고 install 스크립트로 배포한다.
- 기존 테스트를 깨뜨리지 마라.
