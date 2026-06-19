# 윈도우에서 이어서 작업하기 (Norgate 재검증용)

Mac에서 하던 작업은 전부 `origin/main`(GitHub)에 있다. 윈도우에서는 **clone만 하면 그대로 이어진다.**
Norgate Data는 윈도우 전용 데이터 엔진(NDU)이 필요하므로, 생존편향 없는 재검증(step11)은 윈도우가 적합하다.

## 1. 사전 설치 (윈도우)

- **Git for Windows** (Git Bash 포함 — git 훅이 bash 스크립트라 필요)
- **Python 3.11+** (`python --version`). 설치 시 "Add Python to PATH" 체크.
- (선택) **Node 20 LTS** — 프론트엔드 작업 시.
- **Claude Code** — 이 세션을 이어가려면 윈도우에 설치 후 clone한 폴더에서 실행.

## 2. 저장소 clone & 파이썬 환경

PowerShell 기준:
```powershell
git clone https://github.com/Paul-sinn/trading_bot.git
cd trading_bot

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # (cmd면 .venv\Scripts\activate.bat)
pip install -r requirements.txt        # pandas/numpy/langgraph/pytest 등
pip install yfinance                    # v1 무료데이터용(선택)

# 테스트 확인 (367 통과해야 함)
python -m pytest -q
```

> ⚠️ **경로 차이**: Mac은 `.venv/bin/python`, 윈도우는 `.venv\Scripts\python.exe`.
> 문서·커맨드의 `.venv/bin/python`은 윈도우에서 `.\.venv\Scripts\python` 으로 바꿔 읽는다.

## 3. 스크립트 실행 시 PYTHONPATH

`scripts/`를 직접 실행하면 루트를 path에 넣어야 한다(pytest는 pyproject가 처리, 스크립트는 수동):
```powershell
$env:PYTHONPATH = "."
python scripts\run_v1.py --universe AAPL MSFT SMH --start 2015-01-01 --end 2024-12-31
```
(cmd면 `set PYTHONPATH=.` 후 실행. 또는 `python -m scripts.run_v1 ...`.)

## 4. git 훅 설치 (선택, 권장)

pre-commit(lint/구문체크)·pre-push(전체 테스트) 훅. **Git Bash**에서:
```bash
bash scripts/install-hooks.sh
```
훅은 윈도우 venv 경로(`.venv/Scripts`)와 `python`/`python3` 둘 다 인식하도록 돼 있다.

## 5. Norgate Data 설정 (생존편향 없는 재검증, step11)

1. **Norgate Data 구독** + **Norgate Data Updater(NDU)** 윈도우 앱 설치 → 백그라운드 데이터 엔진 실행.
2. 파이썬 패키지: `pip install norgatedata` (로컬 NDU에 접속).
3. `agents/data_adapter.py`의 `NorgateProvider` 스켈레톤을 실연동으로 채운다(지연 import 구조는 준비됨).
   - 또는 더 간단히: NDU/엑셀로 **상폐종목 포함 CSV를 export** 후 `CsvPointInTimeProvider` 경로 사용(아래).

### CSV 드롭인 경로 (벤더 무관, 1순위)
`data/survivorship_free/`에 아래 구조로 둔다 (이 폴더는 `.gitignore`됨 — 라이선스상 커밋 금지):
```
data/survivorship_free/
  metrics.csv          # symbol,listed_from,delisted_at,avg_dollar_volume,atr_pct,is_leveraged_or_inverse
  ohlcv/<SYMBOL>.csv   # date,open,high,low,close,volume   ← 상폐종목도 포함!
  vix.csv              # date,close
```
실행:
```powershell
$env:PYTHONPATH = "."
python scripts\run_oos.py --root data\survivorship_free
```
→ 약세장(2018/2020/2022) + full OOS: 전략 Sharpe/CAGR/MDD vs QQQ + 게이트 PASS/FAIL. working fraction 0.015.

## 6. 작업 이어가기 체크리스트

- [ ] clone + venv + `pytest -q` 367 통과 확인
- [ ] (재검증) Norgate NDU 설치·구독 또는 CSV export 준비 → `data/survivorship_free/`
- [ ] `scripts\run_oos.py` 실행 → 약세장 포함 결과 확인
- [ ] **사람 판정(헌장 §10)**: 편향 제거·약세장 後에도 QQQ를 위험조정으로 이기는가? → 통과 시 **소액 라이브**(페이퍼 단계는 생략 — 헌장 §10 ③, 2026-06 결정)

## 주의 (변하지 않는 원칙)

- `.env`(시크릿)·`data/`(벤더 데이터)는 **절대 커밋 금지**(`.gitignore` 처리됨).
- 생존편향 제거·OOS 통과 전 **라이브 greenlight 금지**(헌장 §3·§10). 자동 라이브 진입 코드 없음.
- 별도 브랜치 `feat-langgraph-orchestration`(에이전트 그래프)는 미머지 상태 — 필요 시 윈도우에서 `git checkout`.
```
git fetch origin
git checkout feat-langgraph-orchestration   # langgraph 작업 이어갈 때
```
