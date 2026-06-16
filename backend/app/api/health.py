"""`/health` 헬스 체크 라우터."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """서버 생존 확인. 항상 `{"status": "ok"}`."""
    return {"status": "ok"}
