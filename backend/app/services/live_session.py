"""라이브 트레이딩 세션 매니저 — 자동화 상태 + Start/Stop/Emergency-Halt 오케스트레이션.

`UI 버튼 → FastAPI(/api/live/*) → LiveSessionManager → (실행 경로) → Robinhood MCP 어댑터`의
중심 레이어. 세션 상태는 **in-memory**(프로세스 스코프)다 — 백엔드 재시작 시 자동화는 false로
리셋되어 재시작이 자동매매를 절대 재개하지 않는다(fail-safe).

CRITICAL 불변식:
- 실주문 없음: `real_orders_placed`는 항상 0. MCP 미연동 시 start는 `NOT_READY_NO_MCP`(크래시 없음).
- stop/emergency-halt는 즉시 신규 주문을 차단한다(`can_place_new_order` 중앙 초크포인트).
- Shadow Report와 분리: live 산출물은 `reports/live_*.jsonl` 전용. shadow 파일에 쓰지 않는다.
- 포지션 자동청산 안 함(별도 청산 엔드포인트는 추후). LLM/RiskGate/베이스라인 미변경.

spec: specs/live_session.md
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.services.live_records import (
    LiveDailyRecord,
    LiveWeeklyRecord,
    aggregate_weekly,
    append_session_event,
    load_daily_records,
    upsert_daily_record,
)
from backend.app.services.robinhood_mcp import (
    RobinhoodMcpAdapter,
    RobinhoodMcpNotConfigured,
    get_mcp_adapter,
)

TradingMode = Literal["report_only", "live_auto"]

# 액션 결과 상태 코드.
STATUS_OK = "OK"
STATUS_NOT_READY_NO_MCP = "NOT_READY_NO_MCP"
STATUS_BLOCKED_LIVE_DISABLED = "BLOCKED_LIVE_DISABLED"
STATUS_BLOCKED_EMERGENCY_HALT = "BLOCKED_EMERGENCY_HALT"
STATUS_BLOCKED_INVALID_MODE = "BLOCKED_INVALID_MODE"

_VALID_MODES: tuple[TradingMode, ...] = ("report_only", "live_auto")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveSessionState(BaseModel):
    """라이브 세션 상태 스냅샷. `real_orders_placed`는 불변식상 항상 0."""

    automation_running: bool = False
    trading_mode: TradingMode = "report_only"
    session_id: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    stop_reason: str | None = None
    emergency_halt: bool = False
    live_enabled: bool = False
    broker_connected: bool = False
    last_heartbeat: str | None = None
    real_orders_placed: int = 0
    daily_order_count: int = 0
    current_exposure: float = 0.0


class LiveActionResult(BaseModel):
    """start/stop/emergency-halt 결과. status로 UI/테스트가 분기한다."""

    status: str
    state: LiveSessionState
    real_orders_placed: int = 0


class LiveSessionManager:
    """in-memory 세션 상태 + 안전 전이. 어댑터/설정은 테스트를 위해 주입 가능."""

    def __init__(
        self,
        *,
        adapter: RobinhoodMcpAdapter | None = None,
        settings: Settings | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self._adapter = adapter
        self._settings = settings
        self._reports_dir = reports_dir
        self._state = LiveSessionState()

    # --- 의존성 헬퍼(주입 우선, 없으면 fresh) ---
    def _get_settings(self) -> Settings:
        return self._settings or Settings()

    def _get_adapter(self) -> RobinhoodMcpAdapter:
        return self._adapter or get_mcp_adapter(self._get_settings())

    def _adapter_available(self, adapter: RobinhoodMcpAdapter) -> bool:
        try:
            return bool(adapter.check_availability())
        except Exception:
            return False  # 어댑터 점검 실패 → 미가용(fail-closed)

    # --- 중앙 초크포인트: stop/halt가 즉시 신규 주문을 막는 단일 지점 ---
    def can_place_new_order(self) -> bool:
        return self._state.automation_running and not self._state.emergency_halt

    # --- 라이브 기록 조회(읽기 전용 — 매니저 reports_dir와 일관). 주문 없음. ---
    def daily_record(self, date: str | None = None) -> LiveDailyRecord | None:
        records = load_daily_records(reports_dir=self._reports_dir)
        if not records:
            return None
        if date is not None:
            for rec in records:
                if rec.date == date:
                    return rec
            return None
        return records[-1]

    def weekly_records(self) -> list[LiveWeeklyRecord]:
        return aggregate_weekly(load_daily_records(reports_dir=self._reports_dir))

    # --- 읽기 전용: UI 새로고침이 매매를 시작하지 않는다 ---
    def status(self) -> LiveSessionState:
        settings = self._get_settings()
        adapter = self._get_adapter()
        # 비파괴 갱신만(상태 전이 없음).
        self._state.live_enabled = settings.live_trading_enabled
        self._state.broker_connected = self._adapter_available(adapter)
        self._state.last_heartbeat = _now_iso()
        self._state.real_orders_placed = 0  # 불변식 강제
        return self._state.model_copy()

    def start(self, mode: TradingMode = "report_only") -> LiveActionResult:
        settings = self._get_settings()
        self._state.live_enabled = settings.live_trading_enabled

        # preflight (하나라도 실패 시 automation_running 불변)
        if mode == "live_auto" and not settings.live_trading_enabled:
            return self._result(STATUS_BLOCKED_LIVE_DISABLED)
        if self._state.emergency_halt:
            return self._result(STATUS_BLOCKED_EMERGENCY_HALT)
        if mode not in _VALID_MODES:
            return self._result(STATUS_BLOCKED_INVALID_MODE)

        adapter = self._get_adapter()
        available = self._adapter_available(adapter)
        self._state.broker_connected = available
        if not available:
            # MCP 미연동 → 크래시 없이 명확한 NOT_READY. automation_running 불변(false).
            return self._result(STATUS_NOT_READY_NO_MCP)

        # 어댑터 있으면 계좌/매수력/포지션 프로브(실패해도 주문 경로 없음).
        try:
            adapter.get_account_status()
            adapter.get_buying_power()
            adapter.get_positions()
        except RobinhoodMcpNotConfigured:
            return self._result(STATUS_NOT_READY_NO_MCP)

        # preflight 통과 → 세션 시작.
        self._state.trading_mode = mode
        self._state.session_id = uuid.uuid4().hex
        self._state.started_at = _now_iso()
        self._state.stopped_at = None
        self._state.stop_reason = None
        self._state.automation_running = True
        self._state.daily_order_count = 0
        self._state.real_orders_placed = 0
        self._write_session_event("start")
        return self._result(STATUS_OK)

    def stop(self, reason: str = "manual") -> LiveActionResult:
        # 즉시 신규 주문 차단.
        self._state.automation_running = False
        self._state.stop_reason = reason
        self._state.stopped_at = _now_iso()
        self._try_cancel_open_orders()  # 어댑터 있으면 시도(없으면 흡수). 청산은 안 함.
        self._write_session_event("stop")
        self._upsert_daily_record()
        return self._result(STATUS_OK)

    def emergency_halt(self) -> LiveActionResult:
        self._state.emergency_halt = True
        self._state.automation_running = False
        self._state.stop_reason = "emergency_halt"
        self._state.stopped_at = _now_iso()
        self._try_cancel_open_orders()
        self._write_session_event("emergency_halt")
        self._upsert_daily_record()
        return self._result(STATUS_OK)

    # --- 내부 헬퍼 ---
    def _try_cancel_open_orders(self) -> None:
        """어댑터가 가용일 때만 미체결 주문 취소를 시도한다(없으면 안전 흡수). 포지션 청산 아님."""
        adapter = self._get_adapter()
        if not self._adapter_available(adapter):
            return
        try:
            adapter.cancel_open_orders()
        except RobinhoodMcpNotConfigured:
            return  # 미연동 → 흡수(상태 전이는 이미 완료)

    def _write_session_event(self, event: str) -> None:
        append_session_event(
            {
                "event": event,
                "ts": _now_iso(),
                "session_id": self._state.session_id,
                "trading_mode": self._state.trading_mode,
                "automation_running": self._state.automation_running,
                "emergency_halt": self._state.emergency_halt,
                "stop_reason": self._state.stop_reason,
                "real_orders_placed": 0,
            },
            reports_dir=self._reports_dir,
        )

    def _upsert_daily_record(self) -> None:
        today = (self._state.stopped_at or _now_iso())[:10]
        record = LiveDailyRecord(
            date=today,
            session_ids=[self._state.session_id] if self._state.session_id else [],
            started_at=self._state.started_at,
            stopped_at=self._state.stopped_at,
            orders_submitted=self._state.daily_order_count,
            stop_reason=self._state.stop_reason,
            notes="no broker connected" if not self._state.broker_connected else "",
        )
        upsert_daily_record(record, reports_dir=self._reports_dir)

    def _result(self, status: str) -> LiveActionResult:
        return LiveActionResult(status=status, state=self._state.model_copy(), real_orders_placed=0)


# --- 프로세스 스코프 싱글톤(API가 사용) ---
_manager: LiveSessionManager | None = None


def get_session_manager() -> LiveSessionManager:
    global _manager
    if _manager is None:
        _manager = LiveSessionManager()
    return _manager


def set_session_manager(manager: LiveSessionManager | None) -> None:
    """테스트 훅 — 주입된 매니저(어댑터/설정/임시 reports_dir 포함)로 교체/리셋."""
    global _manager
    _manager = manager
