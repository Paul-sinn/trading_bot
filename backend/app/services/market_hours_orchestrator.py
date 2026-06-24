"""장중 오케스트레이터 v1 — 감독 자동매매(주문 제출 없음).

정규장 동안 자동으로: 브로커 스냅샷 신선도 확인 → report_only 라이브 스캔 1회 → 자동 주문 라우터 →
(라우터가 후보 선택 시) Discord 승인 요청 생성. **실주문은 절대 직접 내지 않는다.**

CRITICAL 안전 불변식:
- 주문/매수/매도/취소/review 없음. Robinhood write/order MCP 도구 import·호출 없음.
- 오케스트레이터는 **승인 요청만** 만든다. 승인은 리스크 게이트를 우회하지 않는다.
- `real_orders_placed=0` 항상. unsupervised auto-trading 없음.
- 장마감/일일 캡/대기 승인/스냅샷 stale/Discord 봇 미설정 시 안전하게 skip·block한다.

spec: specs/real_order_v1_checklist.md §12
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.approval_store import (
    count_requests_today,
    decisions_for,
    effective_status,
    load_requests,
)
from backend.app.services.broker_snapshot import is_stale, latest_snapshot
from backend.app.services.order_router import OrderRouterResult, select_and_route
from backend.app.services.real_order_executor import daily_real_order_count, is_market_open

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
ORCHESTRATOR_EVENTS_LOG = "orchestrator_events.jsonl"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class OrchestratorEvent(BaseModel):
    """오케스트레이터 1회 실행 결과(주문 아님 — real_orders_placed 항상 0)."""

    timestamp: str = Field(default_factory=_now_iso)
    event_type: str = "run_once"
    market_open: bool = False
    action: str = ""  # skip | run | approval_requested | router_blocked | warn
    result: str = ""  # market_closed | snapshot_stale | daily_real_cap | ... | selected | blocked
    reason: str = ""
    router_decision: str | None = None
    approval_id: str | None = None
    real_orders_placed: int = 0
    errors: list[str] = Field(default_factory=list)

    def model_post_init(self, _ctx) -> None:
        object.__setattr__(self, "real_orders_placed", 0)


def _events_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / ORCHESTRATOR_EVENTS_LOG


def append_event(event: OrchestratorEvent, *, reports_dir: Path | None = None) -> OrchestratorEvent:
    path = _events_path(reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")
    return event


def load_events(*, limit: int = 50, reports_dir: Path | None = None) -> list[OrchestratorEvent]:
    limit = max(1, min(int(limit), 500))
    path = _events_path(reports_dir)
    if not path.exists():
        return []
    out: list[OrchestratorEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(OrchestratorEvent.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_event(*, reports_dir: Path | None = None) -> OrchestratorEvent | None:
    evs = load_events(limit=1, reports_dir=reports_dir)
    return evs[-1] if evs else None


# --- 보조 게이트 ---
def pending_approval_id(*, reports_dir: Path | None = None, now: datetime | None = None) -> str | None:
    """현재 PENDING 상태인 승인 요청 id(있으면). 중복 승인 요청 방지용."""
    now = now or _now()
    for req in reversed(load_requests(limit=500, reports_dir=reports_dir)):
        if effective_status(req, decisions_for(req.approval_id, reports_dir=reports_dir), now=now) == "PENDING":
            return req.approval_id
    return None


def discord_worker_ready(settings: Settings) -> bool:
    """Discord 승인 봇 env가 모두 설정됐는지(토큰/채널/허용 사용자). 값은 노출하지 않는다."""
    return bool(
        (settings.discord_bot_token or "").strip()
        and (settings.discord_approval_channel_id or "").strip()
        and (settings.discord_allowed_user_ids or "").strip()
    )


def _ensure_report_only_session() -> None:
    """report_only 라이브 세션이 돌고 있지 않으면 시작한다(주문 없음). 기본 scan_fn."""
    from backend.app.services.live_session import get_session_manager

    mgr = get_session_manager()
    if not mgr.status().automation_running:
        mgr.start("report_only")


# --- 오케스트레이터 ---
ScanFn = Callable[[], None]
RouterFn = Callable[[], OrderRouterResult]


class MarketHoursOrchestrator:
    """장중 1회 실행/루프 오케스트레이터(주문 제출 없음)."""

    def __init__(self, *, reports_dir: Path | None = None) -> None:
        self._reports_dir = reports_dir
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_event: OrchestratorEvent | None = None

    @property
    def running(self) -> bool:
        return self._running

    def run_once(
        self,
        *,
        settings: Settings | None = None,
        now: datetime | None = None,
        market_open: bool | None = None,
        scan_fn: ScanFn | None = None,
        router_fn: RouterFn | None = None,
    ) -> OrchestratorEvent:
        """1회 오케스트레이션. 게이트 통과 시 스캔→라우터→승인요청. 결과 이벤트를 기록·반환한다."""
        settings = settings or Settings()
        now = now or _now()
        reports_dir = self._reports_dir

        def _emit(action: str, result: str, *, reason: str = "", router_decision: str | None = None,
                  approval_id: str | None = None, mo: bool = False, errors: list[str] | None = None) -> OrchestratorEvent:
            ev = OrchestratorEvent(
                market_open=mo, action=action, result=result, reason=reason,
                router_decision=router_decision, approval_id=approval_id, errors=errors or [],
            )
            append_event(ev, reports_dir=reports_dir)
            self._last_event = ev
            return ev

        # 1) 장시간
        mo = is_market_open(now) if market_open is None else market_open
        if settings.orchestrator_market_hours_only and not mo:
            return _emit("skip", "market_closed", reason="정규장 아님 — 안전 skip", mo=mo)

        # 2) 스냅샷 신선도
        if settings.orchestrator_require_fresh_broker_snapshot:
            snap = latest_snapshot(reports_dir=reports_dir)
            if snap is None:
                return _emit("skip", "snapshot_missing", reason="broker snapshot 없음", mo=mo)
            if is_stale(snap, max_age_seconds=settings.broker_snapshot_max_age_seconds, now=now):
                return _emit("skip", "snapshot_stale", reason="broker snapshot stale", mo=mo)

        # 3) 일일 실주문 캡
        if daily_real_order_count(reports_dir=reports_dir, now=now) >= settings.max_real_orders_per_day:
            return _emit("skip", "daily_real_cap", reason="MAX_REAL_ORDERS_PER_DAY 도달", mo=mo)

        # 4) 일일 승인 요청 캡(오케스트레이터 한도)
        if count_requests_today(reports_dir=reports_dir, now=now) >= settings.orchestrator_max_approvals_per_day:
            return _emit("skip", "daily_approval_cap", reason="ORCHESTRATOR_MAX_APPROVALS_PER_DAY 도달", mo=mo)

        # 5) 이미 대기 중 승인
        pending = pending_approval_id(reports_dir=reports_dir, now=now)
        if pending is not None:
            return _emit("skip", "approval_pending", reason="이미 대기 중 승인 존재", approval_id=pending, mo=mo)

        # 6) Discord 봇 준비
        if settings.orchestrator_require_discord_approval_worker and not discord_worker_ready(settings):
            return _emit("warn", "discord_worker_not_ready",
                         reason="Discord 승인 봇 env 미설정 — 승인 요청 생성 안 함(주문 없음)", mo=mo)

        # 7) 스캔 1회(report_only) → 라우터
        errors: list[str] = []
        try:
            (scan_fn or _ensure_report_only_session)()
        except Exception as exc:  # noqa: BLE001 - 스캔 실패가 오케스트레이터를 죽이지 않게
            errors.append(f"scan: {type(exc).__name__}")
        router = (router_fn or (lambda: select_and_route(
            settings=settings, reports_dir=reports_dir, now=now, market_open=mo)))()

        if router.decision == "ROUTER_SELECTED":
            return _emit("approval_requested", "selected", router_decision=router.decision,
                         approval_id=router.approval_id, reason=router.reason, mo=mo, errors=errors)
        return _emit("router_blocked", "blocked", router_decision=router.decision,
                     reason=router.reason, mo=mo, errors=errors)

    # --- 백그라운드 루프(안전 interval) ---
    def start(self, *, settings: Settings | None = None) -> dict:
        settings = settings or Settings()
        if self._running:
            return {"running": True, "started": False, "reason": "already running"}
        self._running = True
        self._stop.clear()
        interval = max(30, int(settings.orchestrator_interval_seconds))
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="market-orchestrator", daemon=True)
        self._thread.start()
        return {"running": True, "started": True, "interval_seconds": interval}

    def _loop(self, interval: int) -> None:
        # 첫 실행은 즉시, 이후 interval마다. stop()까지 반복.
        try:
            self.run_once()
        except Exception:  # noqa: BLE001
            pass
        while not self._stop.wait(interval):
            if not self._running:
                break
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> dict:
        self._running = False
        self._stop.set()
        return {"running": False, "stopped": True}


_orchestrator: MarketHoursOrchestrator | None = None


def get_orchestrator() -> MarketHoursOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MarketHoursOrchestrator()
    return _orchestrator


def set_orchestrator(o: MarketHoursOrchestrator | None) -> None:
    global _orchestrator
    _orchestrator = o


# --- 읽기 전용 상태(API/UI) ---
class OrchestratorStatus(BaseModel):
    enabled: bool
    running: bool
    market_open: bool
    interval_seconds: int
    market_hours_only: bool
    max_approvals_per_day: int
    require_discord_approval_worker: bool
    discord_worker_ready: bool
    approvals_today: int
    real_orders_today: int
    pending_approval_id: str | None = None
    last_run_at: str | None = None
    last_action: str | None = None
    last_result: str | None = None
    last_reason: str | None = None
    last_router_decision: str | None = None
    real_orders_placed: int = 0


def orchestrator_status(*, settings: Settings | None = None, reports_dir: Path | None = None,
                        now: datetime | None = None) -> OrchestratorStatus:
    settings = settings or Settings()
    now = now or _now()
    last = latest_event(reports_dir=reports_dir)
    return OrchestratorStatus(
        enabled=settings.orchestrator_enabled,
        running=get_orchestrator().running,
        market_open=is_market_open(now),
        interval_seconds=settings.orchestrator_interval_seconds,
        market_hours_only=settings.orchestrator_market_hours_only,
        max_approvals_per_day=settings.orchestrator_max_approvals_per_day,
        require_discord_approval_worker=settings.orchestrator_require_discord_approval_worker,
        discord_worker_ready=discord_worker_ready(settings),
        approvals_today=count_requests_today(reports_dir=reports_dir, now=now),
        real_orders_today=daily_real_order_count(reports_dir=reports_dir, now=now),
        pending_approval_id=pending_approval_id(reports_dir=reports_dir, now=now),
        last_run_at=last.timestamp if last else None,
        last_action=last.action if last else None,
        last_result=last.result if last else None,
        last_reason=last.reason if last else None,
        last_router_decision=last.router_decision if last else None,
        real_orders_placed=0,
    )
