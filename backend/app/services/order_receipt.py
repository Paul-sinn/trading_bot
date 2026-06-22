"""OrderIntent 워커 계약 v0 — **dry-run 영수증 전용**(실주문 없음).

Claude/Codex MCP 워커가 `reports/live_order_intents.jsonl`의 dry-run OrderIntent를 읽고,
control_flags + 최신 broker snapshot으로 재검증한 뒤 `reports/live_order_receipts.jsonl`에
**영수증만** append한다. 어떤 경우에도 Robinhood write/order 도구를 부르지 않으며 브로커 상태를
바꾸지 않는다.

CRITICAL 불변식(모든 영수증):
- `broker_order_id=None`, `real_order_placed=False`, `real_orders_placed=0`, `mode=dry_run_receipt_only`.
- 시크릿/토큰/전체 계정번호 미저장.
- idempotency_key 멱등: 이미 영수증이 있으면 다시 쓰지 않는다(중복 방지).
- control_flags/스냅샷이 없으면 fail-closed로 BLOCKED.

spec: specs/broker_snapshot_bridge.md · specs/live_decision_pipeline.md
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale, latest_snapshot
from backend.app.services.candidate_pipeline import ORDER_INTENTS_LOG
from backend.app.services.control_flags import ControlFlags, read_control_flags
from backend.app.services.execution_gate import OrderIntent

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
RECEIPTS_LOG = "live_order_receipts.jsonl"

ReceiptDecision = Literal["WOULD_SUBMIT", "BLOCKED", "SKIPPED", "ERROR"]
ReceiptSource = Literal["claude_code_worker", "codex_worker"]
RECEIPT_MODE = "dry_run_receipt_only"

WOULD_SUBMIT_REASON = (
    "Dry-run only: order would have been submitted if live execution were enabled"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderReceipt(BaseModel):
    """dry-run 주문 영수증. **주문 아님** — broker_order_id None, real_order_placed False."""

    receipt_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = Field(default_factory=_now_iso)
    source: str = "claude_code_worker"
    mode: str = RECEIPT_MODE
    intent_id: str
    idempotency_key: str
    symbol: str
    side: str = "BUY"
    quantity: float | None = None
    limit_price: float | None = None
    notional: float | None = None
    decision: ReceiptDecision
    reason: str = ""
    broker_order_id: None = None
    real_order_placed: bool = False
    real_orders_placed: int = 0
    control_flags_checked: bool = False
    broker_snapshot_checked: bool = False
    errors: list[str] = Field(default_factory=list)

    def model_post_init(self, _context) -> None:
        # 불변식 강제: 어떤 경로로도 실주문 흔적이 새지 않는다.
        object.__setattr__(self, "broker_order_id", None)
        object.__setattr__(self, "real_order_placed", False)
        object.__setattr__(self, "real_orders_placed", 0)
        object.__setattr__(self, "mode", RECEIPT_MODE)


# --- 저장소(append-only jsonl) ---
def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / RECEIPTS_LOG


def append_receipt(receipt: OrderReceipt, *, reports_dir: Path | None = None) -> OrderReceipt:
    """영수증 1건을 append한다(주문 아님 — 파일 쓰기만)."""
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt.model_dump(), ensure_ascii=False) + "\n")
    return receipt


def load_receipts(*, limit: int = 50, reports_dir: Path | None = None) -> list[OrderReceipt]:
    """최근 영수증들을 읽는다(부재/손상 라인 안전 skip). 최신이 마지막."""
    limit = max(1, min(int(limit), 500))
    path = _path(reports_dir)
    if not path.exists():
        return []
    out: list[OrderReceipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(OrderReceipt.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_receipt(*, reports_dir: Path | None = None) -> OrderReceipt | None:
    """가장 최근 영수증 1건(없으면 None)."""
    rs = load_receipts(limit=1, reports_dir=reports_dir)
    return rs[-1] if rs else None


def receipted_keys(*, reports_dir: Path | None = None) -> set[str]:
    """이미 영수증이 발행된 idempotency_key 집합(멱등 dedup용)."""
    return {r.idempotency_key for r in load_receipts(limit=500, reports_dir=reports_dir)}


# --- pending OrderIntent 로더 ---
def load_pending_intents(*, reports_dir: Path | None = None) -> list[OrderIntent]:
    """`live_order_intents.jsonl`의 dry-run OrderIntent를 읽는다(accepted_dry_run만 actionable).

    파이프라인은 accepted_dry_run intent만 적재하므로 사실상 전부 pending 후보다. 부재/손상 안전.
    """
    path = (reports_dir or DEFAULT_REPORTS_DIR) / ORDER_INTENTS_LOG
    if not path.exists():
        return []
    out: list[OrderIntent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            intent = OrderIntent.model_validate_json(line)
        except (ValueError, TypeError):
            continue
        if intent.execution_gate_status == "accepted_dry_run":
            out.append(intent)
    return out


def _has_open_buy(snapshot: BrokerSnapshot, symbol: str) -> bool:
    for order in snapshot.open_orders:
        if not isinstance(order, dict):
            continue
        if order.get("symbol") == symbol and str(order.get("side", "")).lower() == "buy":
            return True
    return False


def evaluate_intent(
    intent: OrderIntent,
    *,
    control_flags: ControlFlags | None,
    snapshot: BrokerSnapshot | None,
    source: str = "claude_code_worker",
    max_snapshot_age_seconds: int = 3600,
    reject_on_stale_snapshot: bool = False,
) -> OrderReceipt:
    """단일 intent를 control_flags + snapshot으로 재검증해 dry-run 영수증을 만든다.

    **Robinhood write/order 도구를 호출하지 않는다.** 모든 검사는 로컬 파일 상태만 사용.
    """
    notional = intent.planned_notional_usd
    warnings: list[str] = []
    decision: ReceiptDecision = "WOULD_SUBMIT"
    reason = WOULD_SUBMIT_REASON

    # --- 안전 게이트(fail-closed). control_flags/snapshot은 항상 '확인했다'로 기록. ---
    if control_flags is None:
        decision, reason = "BLOCKED", "control_flags 없음 (fail-closed)"
    elif not control_flags.automation_running:
        decision, reason = "BLOCKED", "automation_running=false"
    elif control_flags.emergency_halt:
        decision, reason = "BLOCKED", "emergency_halt=true"
    elif control_flags.block_new_orders:
        decision, reason = "BLOCKED", "block_new_orders=true"
    elif snapshot is None:
        decision, reason = "BLOCKED", "broker snapshot 없음 (fail-closed)"
    else:
        stale = is_stale(snapshot, max_age_seconds=max_snapshot_age_seconds)
        if stale and reject_on_stale_snapshot:
            decision, reason = "BLOCKED", "broker snapshot stale"
        else:
            if stale:
                warnings.append("broker snapshot stale (경고)")
            bp = snapshot.buying_power
            if notional is not None and bp is not None and notional > bp:
                decision, reason = "BLOCKED", f"buying_power 부족: {notional} > {bp}"
            elif _has_open_buy(snapshot, intent.symbol):
                decision, reason = "BLOCKED", f"중복 미체결 매수 주문 존재: {intent.symbol}"

    return OrderReceipt(
        source=source,
        intent_id=intent.scan_event_key,
        idempotency_key=intent.scan_event_key,
        symbol=intent.symbol,
        side=intent.side,
        quantity=intent.planned_quantity,
        limit_price=intent.planned_limit_price,
        notional=notional,
        decision=decision,
        reason=reason,
        control_flags_checked=True,
        broker_snapshot_checked=True,
        errors=warnings,
    )


def process_pending_intents(
    *,
    reports_dir: Path | None = None,
    settings: Settings | None = None,
    source: str = "claude_code_worker",
) -> list[OrderReceipt]:
    """pending intent들을 멱등 처리해 dry-run 영수증을 append한다(이미 처리된 건 skip).

    **실주문/취소/리뷰/브로커 쓰기 없음.** 반환값은 이번 실행에서 새로 쓴 영수증 목록.
    """
    settings = settings or Settings()
    flags = read_control_flags(reports_dir=reports_dir)
    snapshot = latest_snapshot(reports_dir=reports_dir)
    already = receipted_keys(reports_dir=reports_dir)

    written: list[OrderReceipt] = []
    for intent in load_pending_intents(reports_dir=reports_dir):
        if intent.scan_event_key in already:
            continue  # 멱등: 이미 영수증 발행됨 → skip(중복 미발행)
        already.add(intent.scan_event_key)
        try:
            receipt = evaluate_intent(
                intent,
                control_flags=flags,
                snapshot=snapshot,
                source=source,
                max_snapshot_age_seconds=settings.broker_snapshot_max_age_seconds,
                reject_on_stale_snapshot=settings.reject_on_stale_snapshot,
            )
        except Exception as exc:  # noqa: BLE001 - 단일 intent 실패가 워커를 죽이지 않게
            receipt = OrderReceipt(
                source=source,
                intent_id=intent.scan_event_key,
                idempotency_key=intent.scan_event_key,
                symbol=intent.symbol,
                side=intent.side,
                notional=intent.planned_notional_usd,
                decision="ERROR",
                reason="영수증 평가 중 예외",
                control_flags_checked=True,
                broker_snapshot_checked=True,
                errors=[type(exc).__name__],
            )
        append_receipt(receipt, reports_dir=reports_dir)
        written.append(receipt)
    return written
