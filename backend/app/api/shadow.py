"""`/api/shadow` 섀도 리포트 라우터.

reports/ 산출물을 읽어 UI view model을 반환한다. 선택적으로 일간 섀도 리포트를 재생성한다
(report-only — `python -m experiments.daily_shadow_report`만 실행, 절대 주문하지 않음).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스 API 미연결.

spec: specs/shadow_view.md
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.shadow_report import ShadowReportView, load_shadow_report

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parents[3]
# 고정 커맨드 — 클라이언트 입력 없음. 이 모듈만 실행 가능(주문 경로 없음).
_DAILY_CMD = [sys.executable, "-m", "experiments.daily_shadow_report"]
# 날짜는 YYYY-MM-DD만 허용(임의 인자 주입 차단 — 주문 경로 없음).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RunRequest(BaseModel):
    # 선택: 과거 거래일 재생성(러너는 ID 멱등 append — 중복 원장 행 없음). 형식 엄격 검증.
    date: str | None = None


class RunResult(BaseModel):
    ok: bool
    returncode: int
    tail: str
    real_orders_placed: int = 0


def _safe_date(date: str | None) -> str | None:
    """YYYY-MM-DD만 통과(주문 경로 없음 — 임의 인자 차단)."""
    return date if (date and _DATE_RE.match(date)) else None


@router.get("/api/shadow", response_model=ShadowReportView)
async def read_shadow_report(date: str | None = None) -> ShadowReportView:
    """섀도 리포트 view를 반환한다(파일 없음/ malformed 안전).

    date(YYYY-MM-DD) 지정 시 해당 거래일로 필터해 과거 BUY 예시를 읽는다(읽기 전용 — 원장 미변경).
    """
    return load_shadow_report(date=_safe_date(date))


@router.post("/api/shadow/run", response_model=RunResult)
async def run_daily_shadow(req: RunRequest | None = None) -> RunResult:
    """일간 섀도 리포트를 재생성한다(report-only). 고정 커맨드 + 엄격 검증된 --date만 — 주문 절대 없음."""
    cmd = list(_DAILY_CMD)
    if req is not None and req.date is not None:
        if not _DATE_RE.match(req.date):
            return RunResult(ok=False, returncode=-1, tail=f"잘못된 날짜 형식: {req.date} (YYYY-MM-DD)")
        cmd += ["--date", req.date]

    env = {"PYTHONPATH": str(_REPO_ROOT)}
    try:
        proc = subprocess.run(  # noqa: S603 - 고정 인자 + 엄격 검증된 날짜만
            cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True,
            timeout=600, env={**_os_environ(), **env},
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return RunResult(ok=(proc.returncode == 0), returncode=proc.returncode, tail=out[-2000:])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RunResult(ok=False, returncode=-1, tail=f"실행 실패: {exc}")


def _os_environ() -> dict:
    import os
    return dict(os.environ)
