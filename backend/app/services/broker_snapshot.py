"""Broker 스냅샷 스키마 + 저장소 + 빌더 — read-only 워커 브리지 v0.

Claude/Codex MCP 워커가 Robinhood **읽기 전용** 도구(get_accounts/get_portfolio/
get_equity_positions/get_equity_orders/get_equity_quotes)를 호출해 받은 원본을 이 모듈로
**살균(sanitize)** 해 `reports/broker_snapshots.jsonl`에 append-only로 적재한다.

CRITICAL 불변식:
- 주문/취소/쓰기 없음. `real_orders_placed`는 항상 0(강제).
- 계정번호는 마지막 4자리만(`account_last4`). 토큰/시크릿/전체 계정번호 미저장.
- 데이터 위조 금지: MCP 미가용이면 워커가 명확히 실패한다(여기서 가짜 값을 만들지 않는다).
- FastAPI는 이 파일을 **읽기만** 한다(MCP 직접 호출 없음).

spec: specs/robinhood_mcp_readonly.md · reports/fastapi_mcp_feasibility.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.services.robinhood_mcp_readonly import mask_account

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
BROKER_SNAPSHOTS_LOG = "broker_snapshots.jsonl"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class BrokerSnapshot(BaseModel):
    """살균된 브로커 상태 스냅샷(읽기 전용). 주문 아님 — `real_orders_placed`는 항상 0."""

    provider: str = "robinhood-mcp"
    timestamp: str = Field(default_factory=_now_iso)
    source: str = "claude-code-worker"
    account_last4: str = "••••"
    total_value: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    positions: list[dict] = Field(default_factory=list)
    open_orders: list[dict] = Field(default_factory=list)
    quotes: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    real_orders_placed: int = 0

    def model_post_init(self, __context) -> None:  # noqa: D401
        # 불변식 강제: 어떤 경로로 들어와도 real_orders_placed는 0.
        if self.real_orders_placed != 0:
            object.__setattr__(self, "real_orders_placed", 0)


# --- 저장소(append-only jsonl, 읽기 전용 조회) ---
def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / BROKER_SNAPSHOTS_LOG


def append_snapshot(snapshot: BrokerSnapshot, *, reports_dir: Path | None = None) -> BrokerSnapshot:
    """스냅샷을 `broker_snapshots.jsonl`에 append한다(주문 아님 — 파일 쓰기만)."""
    snapshot = snapshot.model_copy(update={"real_orders_placed": 0})  # 불변식 재강제
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot.model_dump(), ensure_ascii=False) + "\n")
    return snapshot


def load_snapshots(*, limit: int = 50, reports_dir: Path | None = None) -> list[BrokerSnapshot]:
    """최근 스냅샷들을 읽는다(부재/손상 라인 안전 skip). 최신이 마지막."""
    limit = max(1, min(int(limit), 500))
    path = _path(reports_dir)
    if not path.exists():
        return []
    out: list[BrokerSnapshot] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(BrokerSnapshot.model_validate_json(line))
        except (ValueError, TypeError):
            continue  # 손상 라인 무시(크래시 없음)
    return out[-limit:]


def latest_snapshot(*, reports_dir: Path | None = None) -> BrokerSnapshot | None:
    """가장 최근 스냅샷 1건(없으면 None)."""
    snaps = load_snapshots(limit=1, reports_dir=reports_dir)
    return snaps[-1] if snaps else None


# --- 신선도(staleness) ---
def snapshot_age_seconds(snapshot: BrokerSnapshot, *, now: datetime | None = None) -> float | None:
    """스냅샷 나이(초). timestamp 파싱 실패 시 None."""
    try:
        ts = datetime.fromisoformat(snapshot.timestamp)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ((now or _now()) - ts).total_seconds()


def is_stale(snapshot: BrokerSnapshot, *, max_age_seconds: int, now: datetime | None = None) -> bool:
    """스냅샷이 max_age_seconds보다 오래됐는지(파싱 불가도 stale로 간주 — fail-closed)."""
    age = snapshot_age_seconds(snapshot, now=now)
    if age is None:
        return True
    return age > max_age_seconds


# --- 원본 MCP 응답 → 살균 스냅샷 빌더 ---
def select_agentic_account(accounts_raw: dict | None) -> dict | None:
    """get_accounts 원본에서 `agentic_allowed=true` 계정을 고른다(없으면 default, 그것도 없으면 첫번째)."""
    if not isinstance(accounts_raw, dict):
        return None
    accounts = (accounts_raw.get("data") or {}).get("accounts") or accounts_raw.get("accounts") or []
    if not isinstance(accounts, list) or not accounts:
        return None
    for acct in accounts:
        if isinstance(acct, dict) and acct.get("agentic_allowed") is True:
            return acct
    for acct in accounts:
        if isinstance(acct, dict) and acct.get("is_default") is True:
            return acct
    return accounts[0] if isinstance(accounts[0], dict) else None


def _data(raw) -> dict:
    return raw.get("data", raw) if isinstance(raw, dict) else {}


def build_snapshot_from_raw(
    raw: dict,
    *,
    provider: str = "robinhood-mcp",
    source: str = "claude-code-worker",
) -> BrokerSnapshot:
    """워커가 모은 원본 MCP 응답 dict를 살균된 BrokerSnapshot으로 변환한다.

    기대 키: accounts, portfolio, positions, open_orders, quotes (각각 해당 read-only 도구의 원본).
    누락/형식 오류는 `errors`에 기록하되 크래시하지 않는다(fail-safe). 전체 계정번호는 절대
    스냅샷에 담지 않는다(마지막 4자리만).
    """
    errors: list[str] = list(raw.get("errors") or [])

    acct = select_agentic_account(raw.get("accounts"))
    account_last4 = mask_account(acct.get("account_number") if acct else None)
    if acct is None:
        errors.append("no account in get_accounts response")

    pf = _data(raw.get("portfolio"))
    total_value = _as_float(pf.get("total_value"))
    cash = _as_float(pf.get("cash"))
    bp = pf.get("buying_power")
    buying_power = _as_float(bp.get("buying_power") if isinstance(bp, dict) else bp)
    if not pf:
        errors.append("missing portfolio data")

    positions = [
        {
            "symbol": p.get("symbol"),
            "quantity": _as_float(p.get("quantity")),
            "average_buy_price": _as_float(p.get("average_buy_price")),
            "shares_available_for_sells": _as_float(p.get("shares_available_for_sells")),
        }
        for p in (_data(raw.get("positions")).get("positions") or [])
        if isinstance(p, dict)
    ]

    open_orders = [
        {
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "state": o.get("state"),
            "quantity": _as_float(o.get("quantity")),
        }
        for o in (_data(raw.get("open_orders")).get("orders") or [])
        if isinstance(o, dict)
    ]

    quotes = []
    for q in _data(raw.get("quotes")).get("results") or []:
        if not isinstance(q, dict):
            continue
        inner = q.get("quote")
        quote: dict = inner if isinstance(inner, dict) else q
        quotes.append(
            {
                "symbol": quote.get("symbol"),
                "price": _as_float(quote.get("last_trade_price") or quote.get("last_non_reg_trade_price")),
                # 자동 라우터의 지정가/스프레드 계산용 호가(있을 때만). 미제공 시 None → 라우터가 fail-closed.
                "bid": _as_float(quote.get("bid_price")),
                "ask": _as_float(quote.get("ask_price")),
                "as_of": quote.get("updated_at") or quote.get("last_trade_price_updated_at"),
            }
        )

    return BrokerSnapshot(
        provider=provider,
        source=source,
        account_last4=account_last4,
        total_value=total_value,
        cash=cash,
        buying_power=buying_power,
        positions=positions,
        open_orders=open_orders,
        quotes=quotes,
        errors=errors,
        real_orders_placed=0,
    )


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
