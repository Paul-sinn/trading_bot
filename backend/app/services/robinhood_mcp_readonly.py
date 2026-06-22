"""Robinhood MCP **읽기 전용** 클라이언트 스켈레톤 — 직접 FastAPI MCP 연동 PoC.

배경(reports/fastapi_mcp_feasibility.md):
- Robinhood MCP는 Streamable HTTP MCP 서버다(`https://agent.robinhood.com/mcp/trading`,
  Claude Code 등록 `type:http`). OAuth 2.0 Bearer로 보호된다(RFC 9728/8414, PKCE S256,
  authorization_code+refresh_token, Dynamic Client Registration 지원).
- 따라서 백엔드가 **직접 MCP 클라이언트**가 되는 것은 기술적으로 가능하나, 최초 인가는
  브라우저 기반 Robinhood 로그인+MFA(대화형)다. 그 단계는 이 태스크 범위 밖이며 자동 실행 금지.

이 모듈의 불변식(CRITICAL):
- **기본 비활성**: `enabled=False`가 기본. 비활성 상태에서 어떤 네트워크/인증도 시도하지 않는다.
- **쓰기 금지(영구)**: place/cancel/review 등 write/action 메서드는 `enabled` 값과 무관하게
  **항상 `ReadOnlyModeError`**를 던진다. 이 클래스에는 주문 경로 자체가 존재하지 않는다.
- **시크릿 미저장/미로그**: 토큰·계정번호 평문을 저장/로그하지 않는다. 계정번호는 마지막 4자리만.
- `real_orders_placed`는 이 경로로 절대 증가하지 않는다(주문 경로 부재).

현재는 인증 트랜스포트가 주입되지 않으면 읽기 메서드도 안전하게 막는다(`RobinhoodMcpNotConfigured`).
실 HTTP/OAuth 트랜스포트 결선은 인가 부트스트랩이 안전하게 해결된 다음 phase에서 한다.

spec: specs/robinhood_mcp_readonly.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from backend.app.core.config import Settings
from backend.app.services.robinhood_mcp import RobinhoodMcpNotConfigured

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
BROKER_SNAPSHOTS_LOG = "broker_snapshots.jsonl"

# 이 클라이언트가 호출을 허용하는 읽기 전용 MCP 도구(화이트리스트).
READ_ONLY_TOOLS: tuple[str, ...] = (
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_orders",
    "get_option_positions",
    "get_equity_quotes",
)

# 이 클라이언트가 **절대** 노출/호출하지 않는 write/action 도구(블랙리스트, 방어적 문서화).
BLOCKED_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "place_equity_order",
        "place_option_order",
        "cancel_equity_order",
        "cancel_option_order",
        "review_equity_order",
        "review_option_order",
        "run_scan",
        "create_scan",
        "update_scan_config",
        "update_scan_filters",
        "create_watchlist",
        "update_watchlist",
        "add_to_watchlist",
        "remove_from_watchlist",
        "add_option_to_watchlist",
        "remove_option_from_watchlist",
        "follow_watchlist",
        "unfollow_watchlist",
    }
)

_NOT_CONFIGURED = "Robinhood MCP read-only client not configured (no authenticated transport)"


class ReadOnlyModeError(RuntimeError):
    """읽기 전용 클라이언트에서 쓰기/주문 경로를 시도했음을 알리는 영구 차단 예외.

    place/cancel/review 등 모든 write 메서드가 `enabled` 여부와 무관하게 이 예외를 던진다.
    주문은 알고리즘 3레이어 → LLM → RiskGate/ExecutionGate를 통과한 별도 경로에서만, 그리고
    검증 완료(헌장 §3·§10) 이후에만 가능하다 — 이 클라이언트로는 영원히 불가능하다.
    """


def mask_account(account_number: str | None) -> str:
    """계정번호를 마지막 4자리만 남기고 마스킹한다(로그/리포트 노출용)."""
    if not account_number:
        return "••••"
    tail = str(account_number)[-4:]
    return f"••••{tail}"


# 트랜스포트 = (tool_name, arguments) -> result dict. 실제 HTTP/MCP 호출은 추후 결선.
McpTransport = Callable[[str, dict], dict]


class RobinhoodMcpReadOnlyClient:
    """Robinhood MCP 읽기 전용 클라이언트.

    기본 비활성·무네트워크. write 메서드는 항상 `ReadOnlyModeError`. 인증 트랜스포트가
    주입되지 않으면 읽기 메서드는 `RobinhoodMcpNotConfigured`로 안전하게 막힌다.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        settings: Settings | None = None,
        transport: McpTransport | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._reports_dir = reports_dir or DEFAULT_REPORTS_DIR
        # 안전 게이트: 명시적 enabled=True + 트랜스포트 주입 둘 다 있어야 읽기 호출이 가능.
        self._enabled = bool(enabled)

    # --- 가용성/도구 목록 ---
    def check_availability(self) -> bool:
        """읽기 호출 가능 여부. enabled + 트랜스포트 둘 다 있을 때만 True(fail-closed)."""
        return self._enabled and self._transport is not None

    def list_tools(self) -> list[str]:
        """노출하는 읽기 전용 도구 목록. write 도구는 절대 포함하지 않는다."""
        return list(READ_ONLY_TOOLS)

    # --- 읽기 전용 호출(트랜스포트 없으면 fail-closed) ---
    def _call_read(self, tool: str, arguments: dict | None = None) -> dict:
        if tool not in READ_ONLY_TOOLS:
            # 화이트리스트 밖 도구는 이 클라이언트로 호출 불가(특히 write 도구).
            raise ReadOnlyModeError(f"tool not allowed in read-only client: {tool}")
        if not self.check_availability():
            raise RobinhoodMcpNotConfigured(_NOT_CONFIGURED)
        return self._transport(tool, arguments or {})  # type: ignore[misc]

    def get_accounts(self) -> dict:
        return self._call_read("get_accounts")

    def get_portfolio(self, account_number: str) -> dict:
        return self._call_read("get_portfolio", {"account_number": account_number})

    def get_positions(self, account_number: str) -> dict:
        return self._call_read("get_equity_positions", {"account_number": account_number})

    def get_open_orders(self, account_number: str) -> dict:
        return self._call_read(
            "get_equity_orders", {"account_number": account_number, "state": "new"}
        )

    def get_quotes(self, symbols: Iterable[str]) -> dict:
        return self._call_read("get_equity_quotes", {"symbols": list(symbols)})

    # --- 스냅샷 영속화(읽기 전용 — 주문 아님) ---
    def write_snapshot(self, snapshot: dict) -> dict:
        """브로커 상태 스냅샷을 `reports/broker_snapshots.jsonl`에 append한다.

        주문을 내지 않는다. `real_orders_placed`는 강제로 0으로 박는다. 계정번호가 있으면
        마지막 4자리만 남기고 마스킹한다(시크릿/평문 노출 방지).
        """
        safe = dict(snapshot)
        safe["real_orders_placed"] = 0
        if "account_number" in safe:
            safe["account_number"] = mask_account(safe.get("account_number"))
        path = self._reports_dir / BROKER_SNAPSHOTS_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
        return safe

    def latest_snapshot(self) -> dict | None:
        """가장 최근 브로커 스냅샷을 읽는다(파일 부재/손상 시 None — 크래시 없음)."""
        path = self._reports_dir / BROKER_SNAPSHOTS_LOG
        if not path.exists():
            return None
        last: dict | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except (ValueError, TypeError):
                continue
        return last

    # --- 영구 차단되는 write/action 경로(enabled 무관, 항상 예외) ---
    def place_equity_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("place_equity_order blocked: read-only client")

    def place_option_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("place_option_order blocked: read-only client")

    def cancel_equity_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("cancel_equity_order blocked: read-only client")

    def cancel_option_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("cancel_option_order blocked: read-only client")

    def review_equity_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("review_equity_order blocked: read-only client")

    def review_option_order(self, *args, **kwargs) -> dict:
        raise ReadOnlyModeError("review_option_order blocked: read-only client")
