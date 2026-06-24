"""`/api/live` 라이브 트레이딩 세션 제어 라우터.

대시보드의 Start/Stop/Emergency-Halt 버튼이 호출한다. 상태/기록 조회는 읽기 전용이며,
**어떤 엔드포인트도 실주문을 내지 않는다**(`real_orders_placed`는 항상 0). Robinhood MCP가
미연동이면 start는 200 + `NOT_READY_NO_MCP`로 응답한다(크래시 없음).

CRITICAL: 외부 브로커 I/O는 service 레이어(LiveSessionManager → Robinhood MCP 어댑터)에만 격리.
Shadow Report와 분리 — 이 라우터는 shadow 파일에 쓰지 않는다.

spec: specs/live_session.md
"""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.candidate_pipeline import AiStatus, Candidate
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.live_records import (
    LiveDailyRecord,
    LiveWeeklyRecord,
)
from backend.app.services.live_scan import ScanEvent
from backend.app.services.live_session import (
    LiveActionResult,
    LiveSessionState,
    TradingMode,
    get_session_manager,
)
from backend.app.services.order_receipt import (
    OrderReceipt,
    latest_receipt,
    load_receipts,
)
from backend.app.services.approval_store import (
    ApprovalView,
    get_request,
    load_requests,
    to_view,
)
from backend.app.services.order_router import (
    OrderRouterResult,
    OrderRouterStatus,
    latest_router_decision,
    router_status,
)
from backend.app.services.real_order_executor import ExecutionStatus, execution_status
from backend.app.services.real_sell_executor import SellExecutionStatus, sell_execution_status

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StartRequest(BaseModel):
    mode: TradingMode = "report_only"


class StopRequest(BaseModel):
    reason: str | None = None


@router.get("/api/live/status", response_model=LiveSessionState)
async def live_status() -> LiveSessionState:
    """현재 라이브 세션 상태(읽기 전용 — 매매를 시작하지 않음)."""
    return get_session_manager().status()


@router.post("/api/live/start", response_model=LiveActionResult)
async def live_start(req: StartRequest | None = None) -> LiveActionResult:
    """라이브 세션 시작. preflight 통과 시에만 automation_running=true. MCP 없으면 NOT_READY_NO_MCP."""
    mode: TradingMode = req.mode if req is not None else "report_only"
    return get_session_manager().start(mode)


@router.post("/api/live/stop", response_model=LiveActionResult)
async def live_stop(req: StopRequest | None = None) -> LiveActionResult:
    """라이브 세션 정지 — 즉시 신규 주문 차단(포지션 자동청산 없음)."""
    reason = (req.reason if req is not None and req.reason else "manual")
    return get_session_manager().stop(reason)


@router.post("/api/live/emergency-halt", response_model=LiveActionResult)
async def live_emergency_halt() -> LiveActionResult:
    """비상 정지 — emergency_halt=true + 즉시 신규 주문 차단."""
    return get_session_manager().emergency_halt()


@router.get("/api/live/daily-record", response_model=LiveDailyRecord | None)
async def live_daily_record(date: str | None = None) -> LiveDailyRecord | None:
    """일간 라이브 기록 조회(읽기 전용 — 주문 없음). date 미지정 시 가장 최근 기록."""
    safe_date = date if (date and _DATE_RE.match(date)) else None
    return get_session_manager().daily_record(safe_date)


@router.get("/api/live/weekly-record", response_model=list[LiveWeeklyRecord])
async def live_weekly_record() -> list[LiveWeeklyRecord]:
    """주간 라이브 기록(일간에서 집계 — 읽기 전용, 주문 없음)."""
    return get_session_manager().weekly_records()


@router.get("/api/live/scan-events", response_model=list[ScanEvent])
async def live_scan_events(limit: int = 50) -> list[ScanEvent]:
    """최근 라이브 스캔 이벤트(읽기 전용 — 스캔 시작 안 함, 주문 없음). limit 1..500 clamp."""
    return get_session_manager().scan_events(limit)


@router.get("/api/live/candidates", response_model=list[Candidate])
async def live_candidates(limit: int = 50) -> list[Candidate]:
    """최근 BUY 후보 + mock LLM 리뷰 결과(읽기 전용 — 리뷰/주문 시작 안 함)."""
    return get_session_manager().candidates(limit)


@router.get("/api/live/order-intents", response_model=list[OrderIntent])
async def live_order_intents(limit: int = 50) -> list[OrderIntent]:
    """최근 dry-run OrderIntent(읽기 전용 — 주문 아님, real_orders_placed=0)."""
    return get_session_manager().order_intents(limit)


@router.get("/api/ai/status", response_model=AiStatus)
async def ai_status() -> AiStatus:
    """AI 예산/쿨다운 셸 상태(읽기 전용 — LLM 호출 없음, ai_cost_estimate_today=0.00)."""
    return get_session_manager().ai_status()


@router.get("/api/live/order-receipts", response_model=list[OrderReceipt])
async def live_order_receipts(limit: int = 50) -> list[OrderReceipt]:
    """워커가 쓴 dry-run 주문 영수증 목록(읽기 전용 — MCP 미호출, 주문 없음). limit 1..500 clamp."""
    return load_receipts(limit=limit)


@router.get("/api/live/order-receipts/latest", response_model=OrderReceipt | None)
async def live_order_receipt_latest() -> OrderReceipt | None:
    """가장 최근 dry-run 주문 영수증(읽기 전용 — MCP 미호출). 없으면 null."""
    return latest_receipt()


@router.get("/api/live/execution-status", response_model=ExecutionStatus)
async def live_execution_status() -> ExecutionStatus:
    """실주문 실행 준비 상태(읽기 전용 — scaffold). 기본 비활성, 실주문 경로 없음."""
    return execution_status()


@router.get("/api/live/sell-execution-status", response_model=SellExecutionStatus)
async def live_sell_execution_status() -> SellExecutionStatus:
    """수동 매도 실행 준비 상태(읽기 전용 — scaffold). 기본 비활성, 매도 경로 없음."""
    return sell_execution_status()


@router.get("/api/live/approvals", response_model=list[ApprovalView])
async def live_approvals(limit: int = 50) -> list[ApprovalView]:
    """Discord 승인 요청 목록 + 실효 상태(읽기 전용 — jsonl만 읽음, Discord/Robinhood 미호출)."""
    return [to_view(r) for r in load_requests(limit=limit)]


@router.get("/api/live/approvals/latest", response_model=ApprovalView | None)
async def live_approval_latest() -> ApprovalView | None:
    """가장 최근 승인 요청 + 실효 상태(읽기 전용). 없으면 null."""
    reqs = load_requests(limit=1)
    return to_view(reqs[-1]) if reqs else None


@router.get("/api/live/approvals/{approval_id}", response_model=ApprovalView | None)
async def live_approval_by_id(approval_id: str) -> ApprovalView | None:
    """approval_id의 승인 요청 + 실효 상태(읽기 전용). 없으면 null."""
    req = get_request(approval_id)
    return to_view(req) if req else None


@router.get("/api/live/order-router/status", response_model=OrderRouterStatus)
async def live_order_router_status() -> OrderRouterStatus:
    """자동 주문 라우터 설정 + 일일 카운트(읽기 전용 — 선택/승인 실행 안 함, 주문 없음)."""
    return router_status()


@router.get("/api/live/order-router/latest", response_model=OrderRouterResult | None)
async def live_order_router_latest() -> OrderRouterResult | None:
    """가장 최근 라우터 결정(읽기 전용 — jsonl만 읽음). 없으면 null."""
    return latest_router_decision()
