"""WebSocket `/ws/ticker` 엔드포인트 + 결정론적 mock 가격 생성기.

이 step에서는 실제 시세/외부 API를 호출하지 않는다 (키 없음, step 범위 밖).
실제 Robinhood MCP 연동은 step 4 이후. 여기서는 심볼별 기준가에 의사난수
워크를 더한 결정론적 mock 가격만 push한다.
"""

import asyncio
import hashlib
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect

# push 주기(초). 테스트에서 빠르게 override 할 수 있도록 모듈 상수로 노출.
TICKER_INTERVAL_SECONDS: float = 1.0

# 기본 워치리스트와 mock 기준가.
DEFAULT_WATCHLIST: list[str] = ["AAPL", "TSLA", "NVDA"]
_BASE_PRICES: dict[str, float] = {"AAPL": 195.0, "TSLA": 240.0, "NVDA": 120.0}


def mock_price(symbol: str, tick: int) -> float:
    """심볼+tick에 대해 결정론적인 mock 가격을 만든다.

    외부 의존성/실난수 없이 해시 기반 의사난수로 기준가 주변을 ±2% 워크한다.
    동일 (symbol, tick)은 항상 동일 가격을 돌려준다.
    """
    base = _BASE_PRICES.get(symbol, 100.0)
    digest = hashlib.sha256(f"{symbol}:{tick}".encode()).hexdigest()
    # 해시 앞 8자리를 [0, 1) 비율로 환산해 ±2% 변동을 만든다.
    ratio = int(digest[:8], 16) / 0xFFFFFFFF
    delta = (ratio - 0.5) * 0.04  # -2% ~ +2%
    return round(base * (1 + delta), 2)


def ticker_snapshot(symbols: list[str], tick: int) -> dict:
    """워치리스트 전체에 대한 ticker 메시지(스키마) 한 건을 만든다."""
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "type": "ticker",
        "data": {
            symbol: {"price": mock_price(symbol, tick), "ts": ts}
            for symbol in symbols
        },
    }


async def ticker_stream(ws: WebSocket, symbols: list[str]) -> None:
    """연결된 클라이언트에 주기적으로 ticker 스냅샷을 push 한다.

    클라이언트가 비정상 종료하면 `WebSocketDisconnect`를 잡아 루프를 종료한다
    (좀비 태스크 방지).
    """
    tick = 0
    try:
        while True:
            await ws.send_json(ticker_snapshot(symbols, tick))
            tick += 1
            await asyncio.sleep(TICKER_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return


def parse_symbols(raw: str | None) -> list[str]:
    """쿼리 파라미터 `symbols`를 워치리스트로 파싱한다.

    - 미지정(None) → 기본 워치리스트.
    - 빈 문자열/공백만 → 빈 리스트(빈 워치리스트 허용).
    - 콤마 구분 문자열 → 대문자 심볼 리스트.
    """
    if raw is None:
        return list(DEFAULT_WATCHLIST)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]
