"""`/api/broker` 읽기 전용 브로커 스냅샷 라우터.

대시보드가 브로커(Robinhood) 계정 상태를 표시하기 위해 호출한다. **이 라우터는 MCP를 직접
호출하지 않는다** — Claude/Codex MCP 워커가 적재한 `reports/broker_snapshots.jsonl`을 읽기만 한다.
어떤 엔드포인트도 주문을 내지 않으며, 스냅샷의 `real_orders_placed`는 항상 0이다.

CRITICAL(ADR-001): 외부 브로커 I/O(인증/조회/주문)는 service 레이어 + 별도 워커에만. 이 라우터는
파일 read-only. 계정번호는 스냅샷 단계에서 이미 마지막 4자리로 마스킹되어 있다.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.services.broker_snapshot import (
    BrokerSnapshot,
    latest_snapshot,
    load_snapshots,
)

router = APIRouter()


@router.get("/api/broker/snapshot", response_model=BrokerSnapshot | None)
async def broker_snapshot() -> BrokerSnapshot | None:
    """최신 브로커 스냅샷(읽기 전용 — MCP 호출 없음). 없으면 null."""
    return latest_snapshot()


@router.get("/api/broker/snapshots", response_model=list[BrokerSnapshot])
async def broker_snapshots(limit: int = 50) -> list[BrokerSnapshot]:
    """최근 브로커 스냅샷 목록(읽기 전용 — MCP 호출 없음). limit 1..500 clamp."""
    return load_snapshots(limit=limit)
