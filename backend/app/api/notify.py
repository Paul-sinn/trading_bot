"""`/api/notify` — Discord 알림 연결 테스트(수동 핑).

매매 이벤트 알림은 backend service(discord_notifier)가 append 지점에서 인라인으로 보낸다.
이 라우터는 **연결 확인용 테스트 메시지**만 보낸다 — 주문/매도/취소 없음. 시크릿 URL은 .env에서만.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.discord_notifier import send_test

router = APIRouter()


class NotifyTestResult(BaseModel):
    configured: bool  # DISCORD_WEBHOOK_URL 설정 여부
    sent: bool        # 테스트 메시지 전송 성공 여부


@router.post("/api/notify/test", response_model=NotifyTestResult)
async def notify_test() -> NotifyTestResult:
    """Discord webhook 연결 테스트(주문 없음). URL 미설정이면 configured=false."""
    result = send_test()
    return NotifyTestResult(**result)
