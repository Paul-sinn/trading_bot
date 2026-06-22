# 맥으로 개발환경 옮기기 (Windows → Mac)

코드는 전부 `origin/main`(GitHub)에 있다. 맥에서는 **clone만 하면 코드는 그대로 이어진다.**
단, `.gitignore`된 항목(벤더 데이터·시크릿·로컬 산출물)은 깃으로 안 넘어가므로 **수동으로 챙겨야 한다.**

## 0. 깃에 없는 것 = 따로 옮겨야 하는 것

`.gitignore`로 제외되어 clone에 포함되지 않는다:

| 항목 | 내용 | 맥에서 처리 |
|---|---|---|
| `data/` | Norgate CSV export (`ndu_export`, `ndu_export_expanded`, `ndu_export_leveraged`) | **윈도우에서 수동 복사** (아래 1단계) — Norgate NDU는 윈도우 전용이라 맥에서 재생성 불가 |
| `.env` | OpenAI/Robinhood 키 | 현재 없음(Mock fallback). 실키 쓸 때만 맥에서 새로 생성 |
| `reports/` | 시뮬 산출물(섀도 리포트 원장 등) | 재생성 가능 — 맥에서 러너 다시 돌리면 생김 |
| `.venv/` | 파이썬 가상환경 | 맥에서 새로 생성(아래 2단계) |
| `node_modules/`, `.next/` | 프론트 의존성·빌드 | 맥에서 `npm install`(아래 4단계) |
| `*.db` | 로컬 SQLite dev DB | 자동 재생성 |

## 1. (윈도우에서) Norgate 데이터 복사 준비 — **가장 중요**

Norgate NDU는 윈도우 전용 데이터 엔진이라 **맥에서는 새 데이터를 못 받는다.** 하지만 이미 export된
CSV(`data/`)는 플랫폼 무관이라 그대로 쓸 수 있다. 윈도우의 아래 폴더를 통째로 맥으로 옮긴다:

```
data/
  ndu_export/
  ndu_export_expanded/      ← run_sim/섀도 리포트 기본 data-root
  ndu_export_leveraged/
```

옮기는 방법(아무거나):
- **클라우드**: OneDrive/Google Drive/Dropbox에 `data/` 업로드 → 맥에서 받아 프로젝트 루트에 둠
- **USB/외장**: `data/` 복사 → 맥에 붙여넣기
- **직접 전송**: 같은 네트워크면 `scp -r data/ user@mac:/path/trading_bot/`

> ⚠️ `data/`는 라이선스상 커밋 금지(`.gitignore`됨). 깃에 올리지 말고 직접 복사할 것.
> 맥에서도 Norgate를 새로 갱신하려면 NDU가 없으므로, **데이터 리프레시는 윈도우에서** 하고 CSV만 맥으로 동기화하는 워크플로가 현실적이다.

## 2. (맥에서) clone & 파이썬 환경

```bash
git clone https://github.com/Paul-sinn/trading_bot.git
cd trading_bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1단계에서 옮긴 data/ 를 프로젝트 루트에 배치한 뒤:
python -m pytest -q          # 전체 통과 확인(현재 기준 backend ~1046 통과 = 이어가기 OK)
```

> ⚠️ **경로 차이(윈도우 ↔ 맥)** — 문서·커맨드 변환표:
> | | 윈도우 | 맥 |
> |---|---|---|
> | 파이썬 | `.\.venv\Scripts\python.exe` | `.venv/bin/python` |
> | activate | `.\.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
> | PYTHONPATH | `$env:PYTHONPATH="."; python ...` | `PYTHONPATH=. python ...` |

## 3. 스크립트/러너 실행 (PYTHONPATH)

`scripts/`·`experiments/`를 직접 실행할 땐 루트를 path에 넣는다(pytest는 pyproject가 처리):

```bash
PYTHONPATH=. python -m experiments.daily_shadow_report                 # 섀도 리포트 재생성
PYTHONPATH=. python scripts/run_oos.py --root data/survivorship_free   # OOS 재검증
```

## 4. 프론트엔드 (맥)

```bash
cd frontend
npm install           # node_modules 재생성 (Node 20 LTS 권장 — 프로젝트 스펙)
npm run dev           # http://localhost:3000
```

백엔드는 루트에서:
```bash
PYTHONPATH=. uvicorn backend.app.main:app --reload   # http://localhost:8000
```

> ⚠️ `npm run dev` 켜둔 채 `npm run build` 금지(같은 `.next` 공유 → 스타일 깨짐). 빌드 검증은 dev 끄고.

## 5. git 훅 설치 (권장)

pre-commit(ESLint/Prettier)·pre-push(전체 테스트) 훅. **맥 터미널**에서:
```bash
bash scripts/install-hooks.sh
```
훅은 맥 venv(`.venv/bin`)·윈도우 venv(`.venv/Scripts`) 경로를 둘 다 인식하도록 돼 있다.

## 6. 맥 이전 체크리스트

- [ ] 윈도우 `data/` (ndu_export*) → 클라우드/USB로 맥에 복사
- [ ] 맥에서 clone + `.venv` + `pip install -r requirements.txt`
- [ ] `data/`를 프로젝트 루트에 배치
- [ ] `python -m pytest -q` 전체 통과
- [ ] `cd frontend && npm install && npm run dev` → `/shadow` 화면 확인
- [ ] (선택) `bash scripts/install-hooks.sh`
- [ ] (선택) 실키 쓸 거면 `.env` 새로 생성 (키 없으면 Mock fallback)

## 주의 (변하지 않는 원칙)

- `.env`(시크릿)·`data/`(벤더 데이터)는 **절대 커밋 금지**(`.gitignore` 처리됨).
- 섀도 리포트/시뮬은 전부 report-only — 브로커/Robinhood/라이브 주문 없음(`real_orders_placed = 0`).
- 생존편향 제거·OOS 통과 전 **라이브 greenlight 금지**(헌장 §3·§10).
