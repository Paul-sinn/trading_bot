"""Discord 승인 봇 워커 — `!approve`/`!reject`/`!status` 명령을 듣고 결정을 기록한다.

CRITICAL 안전 불변식:
- 이 워커는 **Robinhood를 절대 호출하지 않고 주문을 내지 않는다.** approval_decisions.jsonl만 쓴다.
- 허용 사용자 ID(DISCORD_ALLOWED_USER_IDS)만 승인/거부할 수 있다. 그 외는 거부 + 감사 로그.
- 시크릿(봇 토큰)은 .env에서만 읽고 로그/출력에 노출하지 않는다.

실행:
    source .venv/bin/activate
    PYTHONPATH=. python scripts/discord_approval_worker.py

필요 env(.env):
    DISCORD_BOT_TOKEN=...                # 봇 토큰(시크릿 — 출력 금지)
    DISCORD_APPROVAL_CHANNEL_ID=...      # 승인 명령을 받을 채널 ID
    DISCORD_ALLOWED_USER_IDS=123,456     # 승인/거부 허용 사용자 ID(콤마 구분)

`discord.py`가 설치돼 있어야 한다(`pip install discord.py`). 없으면 안내만 출력하고 종료한다.
명령 처리 로직은 backend.app.services.discord_approval.process_approval_command(테스트 가능)에 있다.
"""

from __future__ import annotations

import sys

from backend.app.core.config import Settings
from backend.app.services.discord_approval import process_approval_command


def main() -> int:
    settings = Settings()
    token = settings.discord_bot_token
    channel_id = settings.discord_approval_channel_id
    if not token or not channel_id:
        print("[approval-worker] DISCORD_BOT_TOKEN / DISCORD_APPROVAL_CHANNEL_ID 미설정 — .env를 확인하세요.")
        return 2

    try:
        import discord  # type: ignore
    except ImportError:
        print("[approval-worker] discord.py 미설치 — `pip install discord.py` 후 다시 실행하세요.")
        return 3

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:  # type: ignore[misc]
        # 봇 토큰/유저명은 출력하지 않는다(시크릿 보호). 채널 ID만 확인용으로 표기.
        print(f"[approval-worker] 연결됨 — 채널 {channel_id} 의 !approve/!reject/!status 대기 중. (Robinhood 미연결, 주문 없음)")

    @client.event
    async def on_message(message) -> None:  # type: ignore[misc]
        if message.author.bot:
            return
        if str(message.channel.id) != str(channel_id):
            return
        content = (message.content or "").strip()
        if not content.lower().startswith(("!approve", "!reject", "!status")):
            return
        result = process_approval_command(
            text=content,
            discord_user_id=str(message.author.id),
            discord_username=str(getattr(message.author, "name", "")),
            channel_id=str(message.channel.id),
            message_id=str(message.id),
            settings=settings,
        )
        try:
            await message.channel.send(result["reply"])
        except Exception:  # noqa: BLE001 - 회신 실패가 워커를 죽이지 않게
            pass

    try:
        client.run(token, log_handler=None)  # 토큰은 라이브러리 내부로만 전달 — 로그 미노출
    except discord.PrivilegedIntentsRequired:
        print(
            "[approval-worker] MESSAGE CONTENT INTENT 미활성 — Discord 개발자 포털에서 켜야 합니다.\n"
            "  https://discord.com/developers/applications → 해당 앱 → Bot →\n"
            "  Privileged Gateway Intents → 'MESSAGE CONTENT INTENT' 토글 ON 후 다시 실행.\n"
            "  (관리자 권한과 별개의 설정입니다. 봇이 !approve/!reject 텍스트를 읽으려면 필수.)"
        )
        return 4
    except discord.LoginFailure:
        print("[approval-worker] 로그인 실패 — DISCORD_BOT_TOKEN이 유효한지 .env를 확인하세요. (토큰 미출력)")
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
