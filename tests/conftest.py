"""테스트 전역 설정.

CRITICAL: 테스트는 개발자 로컬 `.env`의 `DISCORD_WEBHOOK_URL`을 **무시**한다. append 지점들이
인라인 알림을 호출하므로, .env에 실제 webhook이 있으면 테스트 도중 실제 Discord로 메시지가 나갈 수
있다. 빈 문자열 env var는 .env 파일보다 우선하고 notifier에서 falsy → no-op이 되어 실제 전송을 막는다.
명시적으로 알림을 검증하는 테스트는 `settings=`로 URL을 주입하거나 `_http_post`를 monkeypatch한다.
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_discord_by_default(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    yield
