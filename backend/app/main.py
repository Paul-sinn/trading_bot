"""FastAPI 진입점.

REST 라우터 등록 + WebSocket 엔드포인트 + 로컬 frontend 허용 CORS.
외부 API(Robinhood MCP / Claude)·DB 접근은 이 backend에만 격리한다 (ADR-001/003).
"""

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import broker, goal_plan, health, live, notify, portfolio, positions, shadow
from backend.app.ws.ticker import parse_symbols, ticker_stream

app = FastAPI(title="Custom Trading Bot API")

# 로컬 Next.js frontend(3000) 허용. 프로덕션 오리진은 후속 step에서 설정.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우트 정의는 api/ 모듈에 두고 여기서 등록한다. FastAPI 0.137의 lazy
# `include_router`는 introspection이 어려운 wrapper를 추가하므로, 미리 만들어진
# 라우트를 직접 등록해 평평한 라우트 목록을 유지한다.
app.router.routes.extend(health.router.routes)
app.router.routes.extend(portfolio.router.routes)
app.router.routes.extend(goal_plan.router.routes)
app.router.routes.extend(shadow.router.routes)
app.router.routes.extend(live.router.routes)
app.router.routes.extend(broker.router.routes)
app.router.routes.extend(positions.router.routes)
app.router.routes.extend(notify.router.routes)


@app.websocket("/ws/ticker")
async def ws_ticker(ws: WebSocket, symbols: str | None = None) -> None:
    """실시간 가격 티커 push. mock 가격 생성기 사용 (step 3 범위)."""
    await ws.accept()
    await ticker_stream(ws, parse_symbols(symbols))
