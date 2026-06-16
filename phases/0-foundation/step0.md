# Step 0: project-setup

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/CLAUDE.md`
- `/docs/PRD.md`
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`

`docs/ARCHITECTURE.md`의 디렉토리 구조를 정확히 따라야 한다.

## 작업

폴리글랏 모노레포의 기반 폴더 구조와 의존성 스캐폴드를 만든다. 이 step은 **구조 생성만** 한다. 실제 비즈니스 로직은 이후 step에서 구현한다.

1. **디렉토리 생성** (ARCHITECTURE.md 구조와 일치):
   - `backend/app/{api,ws,db,cache,core}/` — 각 폴더에 빈 `__init__.py`
   - `agents/` — 빈 `__init__.py`
   - `algorithms/` — 빈 `__init__.py`
   - `specs/` — `.gitkeep`
   - `tests/` — 빈 `__init__.py`
   - `frontend/` — 이 step에서는 디렉토리와 placeholder만. Next.js 풀 스캐폴드는 후속 phase. `frontend/.gitkeep`

2. **Python 의존성** — 루트 `requirements.txt`:
   - `fastapi`, `uvicorn[standard]`, `websockets`, `pydantic`, `pydantic-settings`
   - `sqlalchemy`, `redis`, `httpx`
   - `pandas`, `numpy`
   - 테스트: `pytest`, `pytest-asyncio`
   - 주의: `TA-lib`는 C 라이브러리 의존이라 pip 설치가 환경에 따라 실패한다. `requirements.txt`에는 주석으로 `# ta-lib  (requires: brew install ta-lib)` 로 남기고, **실제 import는 하지 마라**. 알고리즘 step은 pandas/numpy로 지표를 직접 계산한다.

3. **pytest 설정** — 루트 `pyproject.toml` 또는 `pytest.ini`:
   - `testpaths = tests`
   - `pythonpath = .` (루트에서 `backend`, `agents`, `algorithms` import 가능하도록)
   - asyncio mode auto

4. **`backend/app/core/config.py`** — pydantic-settings `Settings` 클래스 시그니처만:
   ```python
   class Settings(BaseSettings):
       robinhood_api_key: str | None = None
       claude_api_key: str | None = None
       database_url: str = "sqlite:///./trading_bot.db"
       redis_url: str = "redis://localhost:6379/0"
       # model_config: .env 로딩, extra=ignore
   ```
   시크릿은 `.env`에서만 읽는다. 하드코딩 금지.

5. **`.env.example`** — 실제 값 없이 키 이름만:
   ```
   ROBINHOOD_API_KEY=
   CLAUDE_API_KEY=
   DATABASE_URL=sqlite:///./trading_bot.db
   REDIS_URL=redis://localhost:6379/0
   ```
   `.env`(실제 시크릿)는 절대 생성/커밋하지 마라.

6. **`.gitignore` 갱신** — 기존 항목 유지하고 다음 추가: `.env`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.db`, `.venv/`, `venv/`.

7. **smoke 테스트** — `tests/test_project_setup.py`:
   - `backend.app.core.config`에서 `Settings`를 import할 수 있고, 기본값(`database_url`이 sqlite)이 맞는지 확인.
   - 디렉토리 구조 존재 확인 (`algorithms`, `agents`, `backend/app` import 가능).

## Acceptance Criteria

```bash
pip install -r requirements.txt
python -c "from backend.app.core.config import Settings; print(Settings().database_url)"
pytest tests/test_project_setup.py -v
pytest --collect-only
```

## 검증 절차

1. 위 AC 커맨드를 실행한다. `pytest`가 테스트를 정상 collect하고 smoke 테스트가 통과해야 한다.
2. 아키텍처 체크리스트:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택(Python 3.11, FastAPI, SQLAlchemy)을 벗어나지 않았는가?
   - CLAUDE.md CRITICAL 규칙(시크릿 비노출)을 위반하지 않았는가?
3. 결과에 따라 `phases/0-foundation/index.json`의 step 0을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "생성된 폴더구조/의존성 한 줄 요약"`
   - 3회 시도 후 실패 → `"status": "error"`, `"error_message"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"blocked_reason"`

## 금지사항

- 실제 `.env` 파일을 생성하지 마라. 이유: 시크릿이 커밋에 노출된다 (CLAUDE.md CRITICAL).
- `import talib` 하지 마라. 이유: C 라이브러리 의존으로 CI/대부분 환경에서 설치 실패한다. pandas/numpy로 대체한다.
- Next.js를 실제로 `create-next-app` 하지 마라. 이유: 이 phase 범위 밖이고 시간이 오래 걸린다. placeholder만 만든다.
- 비즈니스 로직(시그널/주문/에이전트)을 구현하지 마라. 이유: 이후 step의 범위다.
- 기존 테스트를 깨뜨리지 마라.
