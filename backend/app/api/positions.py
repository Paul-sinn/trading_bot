"""`/api/positions` · `/api/exits` 읽기 전용 라우터 (Position & Exit Manager v0).

대시보드가 broker 포지션과 dry-run 청산 판단을 표시하기 위해 호출한다. **MCP를 직접 호출하지
않고** 워커가 적재한 `reports/broker_snapshots.jsonl` / `reports/exit_decisions.jsonl`만 읽는다.
어떤 엔드포인트도 주문/매도를 내지 않는다(모든 ExitDecision: real_orders_placed=0).
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.services.position_manager import (
    ExitDecision,
    Position,
    latest_exit_decision,
    load_exit_decisions,
    read_positions,
)

router = APIRouter()


@router.get("/api/positions", response_model=list[Position])
async def positions() -> list[Position]:
    """최신 broker 스냅샷 기반 포지션(읽기 전용 — MCP 미호출, 주문 없음)."""
    return read_positions()


@router.get("/api/exits/latest", response_model=ExitDecision | None)
async def exit_latest() -> ExitDecision | None:
    """가장 최근 dry-run 청산 판단(읽기 전용 — 매도 없음). 없으면 null."""
    return latest_exit_decision()


@router.get("/api/exits", response_model=list[ExitDecision])
async def exits(limit: int = 50) -> list[ExitDecision]:
    """최근 dry-run 청산 판단 목록(읽기 전용 — 매도 없음). limit 1..500 clamp."""
    return load_exit_decisions(limit=limit)
