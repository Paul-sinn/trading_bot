"""`/api/shadow` 섀도 리포트 라우터.

reports/ 산출물을 읽어 UI view model을 반환한다. 선택적으로 일간 섀도 리포트를 재생성한다
(report-only — `python -m experiments.daily_shadow_report`만 실행, 절대 주문하지 않음).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스 API 미연결.

spec: specs/shadow_view.md
"""

from __future__ import annotations

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


class RunResult(BaseModel):
    ok: bool
    returncode: int
    tail: str
    real_orders_placed: int = 0


@router.get("/api/shadow", response_model=ShadowReportView)
async def read_shadow_report() -> ShadowReportView:
    """섀도 리포트 view를 반환한다(파일 없음/ malformed 안전)."""
    return load_shadow_report()


@router.post("/api/shadow/run", response_model=RunResult)
async def run_daily_shadow() -> RunResult:
    """일간 섀도 리포트를 재생성한다(report-only). 고정 커맨드만 실행 — 주문 절대 없음."""
    env = {"PYTHONPATH": str(_REPO_ROOT)}
    try:
        proc = subprocess.run(  # noqa: S603 - 고정 인자, 클라이언트 입력 없음
            _DAILY_CMD, cwd=str(_REPO_ROOT), capture_output=True, text=True,
            timeout=600, env={**_os_environ(), **env},
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return RunResult(ok=(proc.returncode == 0), returncode=proc.returncode, tail=out[-2000:])
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RunResult(ok=False, returncode=-1, tail=f"실행 실패: {exc}")


def _os_environ() -> dict:
    import os
    return dict(os.environ)
