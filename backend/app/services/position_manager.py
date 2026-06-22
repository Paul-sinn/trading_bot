"""Position & Exit Manager v0 — broker 스냅샷 기반 포지션 추적 + **dry-run 청산 판단**(실주문 없음).

최신 broker 스냅샷의 positions를 진실의 원천으로 삼아 미실현 손익을 계산하고, 잠긴 베이스라인
청산 규칙(stop 0.15 / trail 0.20 / 60일, `agents.order_plan.NORMAL_PROFILE`)으로 **HOLD/청산 시그널만**
만든다. 매도 주문을 절대 내지 않는다.

CRITICAL 불변식:
- 실주문/매도/취소 없음. 모든 ExitDecision: `broker_order_id=None`, `real_order_placed=False`,
  `real_orders_placed=0`.
- backend는 MCP를 호출하지 않는다(워커가 적재한 `reports/broker_snapshots.jsonl`만 읽음).
- 베이스라인 청산 상수를 변경하지 않고 '서술'만 한다(헌장 — 잠긴 베이스라인).

spec: specs/broker_snapshot_bridge.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agents.order_plan import NORMAL_PROFILE, ExitProfile
from backend.app.services.broker_snapshot import BrokerSnapshot, load_snapshots

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
EXIT_DECISIONS_LOG = "exit_decisions.jsonl"

ExitSignal = Literal[
    "HOLD", "STOP_LOSS", "TRAILING_STOP", "TIME_STOP", "MANUAL_CLOSE_DETECTED", "ERROR"
]
PositionStatus = Literal["open", "missing", "manually_closed_detected"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class Position(BaseModel):
    """broker 스냅샷에서 도출한 포지션 상태(읽기 전용 — 주문 아님)."""

    symbol: str
    quantity: float
    average_buy_price: float | None = None
    current_quote: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    peak_price: float | None = None
    entry_source: str = "broker_snapshot"
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    holding_days: int | None = None
    status: PositionStatus = "open"


class ExitDecision(BaseModel):
    """dry-run 청산 판단. **주문 아님** — broker_order_id None, real_order_placed False."""

    timestamp: str = Field(default_factory=_now_iso)
    symbol: str
    quantity: float
    average_buy_price: float | None = None
    current_price: float | None = None
    unrealized_pnl_pct: float | None = None
    exit_signal: ExitSignal
    reason: str = ""
    would_sell_quantity: float = 0.0
    broker_order_id: None = None
    real_order_placed: bool = False
    real_orders_placed: int = 0

    def model_post_init(self, _context) -> None:
        # 불변식 강제: 어떤 경로로도 실주문/매도 흔적 0.
        object.__setattr__(self, "broker_order_id", None)
        object.__setattr__(self, "real_order_placed", False)
        object.__setattr__(self, "real_orders_placed", 0)


def _as_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _quote_map(snapshot: BrokerSnapshot) -> dict[str, float]:
    out: dict[str, float] = {}
    for q in snapshot.quotes:
        if isinstance(q, dict):
            sym, price = q.get("symbol"), _as_float(q.get("price"))
            if sym and price is not None:
                out[str(sym)] = price
    return out


def _days_between(start_iso: str | None, end_iso: str | None) -> int | None:
    try:
        a = datetime.fromisoformat(start_iso)  # type: ignore[arg-type]
        b = datetime.fromisoformat(end_iso)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    return max(0, (b - a).days)


def read_positions(*, reports_dir: Path | None = None) -> list[Position]:
    """최신 스냅샷의 포지션을 읽고, 히스토리로 peak/first_seen을 보강한다(읽기 전용 — MCP 없음)."""
    snaps = load_snapshots(limit=500, reports_dir=reports_dir)
    if not snaps:
        return []
    latest = snaps[-1]
    qmap = _quote_map(latest)

    # 심볼별 히스토리: 최초 등장 시각 + 관측된 최고가(트레일링용).
    first_seen: dict[str, str] = {}
    peak: dict[str, float] = {}
    for snap in snaps:
        qm = _quote_map(snap)
        for p in snap.positions:
            if not isinstance(p, dict):
                continue
            sym = p.get("symbol")
            if not sym:
                continue
            first_seen.setdefault(str(sym), snap.timestamp)
            price = qm.get(str(sym))
            if price is not None:
                peak[str(sym)] = max(peak.get(str(sym), price), price)

    positions: list[Position] = []
    for p in latest.positions:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol"))
        qty = _as_float(p.get("quantity")) or 0.0
        avg = _as_float(p.get("average_buy_price"))
        cur = qmap.get(sym)
        mv = qty * cur if cur is not None else None
        upnl = (mv - qty * avg) if (mv is not None and avg is not None) else None
        upct = ((cur / avg - 1.0) if (cur is not None and avg not in (None, 0)) else None)
        pk = max(peak.get(sym, cur), cur) if cur is not None else peak.get(sym)
        positions.append(
            Position(
                symbol=sym,
                quantity=qty,
                average_buy_price=avg,
                current_quote=cur,
                market_value=mv,
                unrealized_pnl=upnl,
                unrealized_pnl_pct=upct,
                peak_price=pk,
                first_seen_at=first_seen.get(sym, latest.timestamp),
                last_seen_at=latest.timestamp,
                holding_days=_days_between(first_seen.get(sym, latest.timestamp), latest.timestamp),
                status="open",
            )
        )
    return positions


def detect_manual_closes(*, reports_dir: Path | None = None) -> list[dict]:
    """직전 스냅샷엔 있었으나 최신 스냅샷에서 사라진 포지션(수동 청산 추정). 없으면 []."""
    snaps = load_snapshots(limit=500, reports_dir=reports_dir)
    if len(snaps) < 2:
        return []
    prev, latest = snaps[-2], snaps[-1]
    latest_syms = {str(p.get("symbol")) for p in latest.positions if isinstance(p, dict)}
    out: list[dict] = []
    for p in prev.positions:
        if isinstance(p, dict) and str(p.get("symbol")) not in latest_syms:
            out.append(p)
    return out


def evaluate_exit(
    position: Position,
    *,
    profile: ExitProfile = NORMAL_PROFILE,
    now: datetime | None = None,  # noqa: ARG001 - 시그니처 일관성용(현재 holding_days는 사전계산)
) -> ExitDecision:
    """단일 포지션의 dry-run 청산 시그널(매도 주문 없음). 우선순위: stop > trailing > time > hold."""

    def _decision(signal: ExitSignal, reason: str, would_sell: float = 0.0) -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol,
            quantity=position.quantity,
            average_buy_price=position.average_buy_price,
            current_price=position.current_quote,
            unrealized_pnl_pct=position.unrealized_pnl_pct,
            exit_signal=signal,
            reason=reason,
            would_sell_quantity=would_sell,
        )

    avg = position.average_buy_price
    cur = position.current_quote

    if cur is None:
        return _decision("HOLD", "missing quote — cannot evaluate exit")
    if avg is None or avg <= 0:
        return _decision("HOLD", "average_buy_price 없음 — cannot evaluate exit")

    stop_price = avg * (1.0 - profile.stop_loss_pct)
    if cur <= stop_price:
        return _decision("STOP_LOSS", f"stop_loss {profile.stop_loss_pct:.0%}: {cur} <= {stop_price:.4f}", position.quantity)

    peak = position.peak_price
    if peak is not None and peak > 0:
        trail_price = peak * (1.0 - profile.trailing_stop_pct)
        if cur <= trail_price:
            return _decision(
                "TRAILING_STOP",
                f"trailing {profile.trailing_stop_pct:.0%} from peak {peak}: {cur} <= {trail_price:.4f}",
                position.quantity,
            )

    if position.holding_days is not None and position.holding_days >= profile.max_holding_days:
        return _decision("TIME_STOP", f"time stop: {position.holding_days}d >= {profile.max_holding_days}d", position.quantity)

    return _decision("HOLD", "no exit rule triggered")


def make_manual_close_decision(prev_position: dict) -> ExitDecision:
    """사라진 포지션에 대한 MANUAL_CLOSE_DETECTED 판단(주문 없음 — 이미 사라짐)."""
    return ExitDecision(
        symbol=str(prev_position.get("symbol")),
        quantity=_as_float(prev_position.get("quantity")) or 0.0,
        average_buy_price=_as_float(prev_position.get("average_buy_price")),
        current_price=None,
        unrealized_pnl_pct=None,
        exit_signal="MANUAL_CLOSE_DETECTED",
        reason="직전 스냅샷에 있던 포지션이 최신 스냅샷에서 사라짐 (수동 청산 추정)",
        would_sell_quantity=0.0,
    )


# --- 저장소(append-only) ---
def _path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / EXIT_DECISIONS_LOG


def append_exit_decision(decision: ExitDecision, *, reports_dir: Path | None = None) -> ExitDecision:
    path = _path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(decision.model_dump(), ensure_ascii=False) + "\n")
    return decision


def load_exit_decisions(*, limit: int = 50, reports_dir: Path | None = None) -> list[ExitDecision]:
    limit = max(1, min(int(limit), 500))
    path = _path(reports_dir)
    if not path.exists():
        return []
    out: list[ExitDecision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(ExitDecision.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_exit_decision(*, reports_dir: Path | None = None) -> ExitDecision | None:
    ds = load_exit_decisions(limit=1, reports_dir=reports_dir)
    return ds[-1] if ds else None


def run_exit_cycle(
    *, reports_dir: Path | None = None, profile: ExitProfile = NORMAL_PROFILE
) -> list[ExitDecision]:
    """현재 포지션 + 수동청산 감지로 dry-run 청산 판단을 만들어 append한다(매도 주문 없음)."""
    decisions = [evaluate_exit(p, profile=profile) for p in read_positions(reports_dir=reports_dir)]
    decisions += [make_manual_close_decision(p) for p in detect_manual_closes(reports_dir=reports_dir)]
    for d in decisions:
        append_exit_decision(d, reports_dir=reports_dir)
    return decisions
