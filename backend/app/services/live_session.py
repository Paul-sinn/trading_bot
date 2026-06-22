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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.live_records import (
    LiveDailyRecord,
    LiveWeeklyRecord,
    aggregate_weekly,
    append_session_event,
    load_daily_records,
    upsert_daily_record,
)
from backend.app.services.live_scan import LiveScanLoop, ScanEvent, load_scan_events
from backend.app.services.market_data import (
    MarketDataProvider,
    MarketDataProviderNotConfigured,
    get_market_data_provider,
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
STATUS_NOT_READY_BAD_PROVIDER = "NOT_READY_BAD_PROVIDER"
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
    # 라이브 시장데이터 + report_only 스캔 루프 상태(모니터링 전용 — 주문 없음).
    market_data_provider: str = ""
    market_data_status: str = ""
    live_scan_running: bool = False
    last_scan_at: str | None = None
    last_scan_event_count: int = 0
    latest_buy_candidates: list[str] = Field(default_factory=list)


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
        market_data: MarketDataProvider | None = None,
        settings: Settings | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self._adapter = adapter
        self._market_data = market_data
        self._settings = settings
        self._reports_dir = reports_dir
        self._state = LiveSessionState()
        # report_only 스캔 루프(daemon 스레드 + stop flag). 시작 전엔 None.
        self._scan_loop: LiveScanLoop | None = None
        self._scan_thread: threading.Thread | None = None
        self._scan_stop = threading.Event()
        self._scan_lock = threading.Lock()

    # --- 의존성 헬퍼(주입 우선, 없으면 fresh) ---
    def _get_settings(self) -> Settings:
        return self._settings or Settings()

    def _get_adapter(self) -> RobinhoodMcpAdapter:
        return self._adapter or get_mcp_adapter(self._get_settings())

    def _provider_status(self) -> tuple[MarketDataProvider | None, str, str]:
        """(provider, name, status). 알 수 없는 provider → (None, raw_name, 'invalid')."""
        if self._market_data is not None:
            st = self._market_data.provider_status()
            return self._market_data, self._market_data.name, ("available" if st.available else st.detail or "unavailable")
        try:
            provider = get_market_data_provider(self._get_settings())
        except MarketDataProviderNotConfigured:
            return None, str(self._get_settings().market_data_provider), "invalid"
        st = provider.provider_status()
        return provider, provider.name, ("available" if st.available else st.detail or "unavailable")

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

    # --- 읽기 전용: UI 새로고침이 매매(스캔)를 시작하지 않는다 ---
    def status(self) -> LiveSessionState:
        settings = self._get_settings()
        adapter = self._get_adapter()
        # 비파괴 갱신만(상태 전이/스캔 시작 없음).
        self._state.live_enabled = settings.live_trading_enabled
        self._state.broker_connected = self._adapter_available(adapter)
        _, provider_name, provider_status = self._provider_status()
        self._state.market_data_provider = provider_name
        self._state.market_data_status = provider_status
        self._state.last_heartbeat = _now_iso()
        self._state.real_orders_placed = 0  # 불변식 강제
        return self._state.model_copy()

    # --- 스캔 이벤트 조회(읽기 전용 — 스캔 시작 안 함, 주문 없음) ---
    def scan_events(self, limit: int = 50) -> list[ScanEvent]:
        return load_scan_events(limit=limit, reports_dir=self._reports_dir)

    def start(self, mode: TradingMode = "report_only") -> LiveActionResult:
        settings = self._get_settings()
        self._state.live_enabled = settings.live_trading_enabled

        # 공통 preflight (하나라도 실패 시 automation_running 불변)
        if self._state.emergency_halt:
            return self._result(STATUS_BLOCKED_EMERGENCY_HALT)
        if mode not in _VALID_MODES:
            return self._result(STATUS_BLOCKED_INVALID_MODE)

        # 시장데이터 provider는 두 모드 공통 필수. 알 수 없으면 fail-closed.
        provider, provider_name, provider_status = self._provider_status()
        self._state.market_data_provider = provider_name
        self._state.market_data_status = provider_status
        if provider is None:
            return self._result(STATUS_NOT_READY_BAD_PROVIDER)

        if mode == "live_auto":
            # live_auto: LIVE_TRADING_ENABLED + Robinhood MCP 필요(없으면 NOT_READY_NO_MCP).
            if not settings.live_trading_enabled:
                return self._result(STATUS_BLOCKED_LIVE_DISABLED)
            adapter = self._get_adapter()
            available = self._adapter_available(adapter)
            self._state.broker_connected = available
            if not available:
                return self._result(STATUS_NOT_READY_NO_MCP)
            try:
                adapter.get_account_status()
                adapter.get_buying_power()
                adapter.get_positions()
            except RobinhoodMcpNotConfigured:
                return self._result(STATUS_NOT_READY_NO_MCP)
        else:
            # report_only: Robinhood MCP 불요. 시장데이터만으로 모니터링 시작(실주문 경로 없음).
            self._state.broker_connected = self._adapter_available(self._get_adapter())

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
        self._start_scan(provider)
        return self._result(STATUS_OK)

    def stop(self, reason: str = "manual") -> LiveActionResult:
        # 즉시 신규 주문 차단 + 스캔 루프/price polling 중지.
        self._state.automation_running = False
        self._state.stop_reason = reason
        self._state.stopped_at = _now_iso()
        self._stop_scan()
        self._try_cancel_open_orders()  # 어댑터 있으면 시도(없으면 흡수). 청산은 안 함.
        self._write_session_event("stop")
        self._upsert_daily_record()
        return self._result(STATUS_OK)

    def emergency_halt(self) -> LiveActionResult:
        self._state.emergency_halt = True
        self._state.automation_running = False
        self._state.stop_reason = "emergency_halt"
        self._state.stopped_at = _now_iso()
        self._stop_scan()
        self._try_cancel_open_orders()
        self._write_session_event("emergency_halt")
        self._upsert_daily_record()
        return self._result(STATUS_OK)

    def shutdown(self) -> None:
        """프로세스/테스트 teardown용 — 스캔 스레드를 깔끔히 정지(상태 전이 없음)."""
        self._stop_scan()

    # --- report_only 스캔 루프 라이프사이클 (주문/LLM 없음) ---
    def _start_scan(self, provider: MarketDataProvider) -> None:
        settings = self._get_settings()
        if not settings.live_scan_enabled:
            return
        self._scan_loop = LiveScanLoop(
            provider,
            reports_dir=self._reports_dir,
            max_symbols=settings.live_scan_max_symbols_per_cycle,
        )
        # 첫 cycle은 동기 실행(결정론 — status/테스트가 즉시 결과를 본다).
        self._run_one_cycle()
        # 이후 주기적 재스캔은 daemon 스레드(automation_running 동안). interval마다 1 cycle.
        self._scan_stop.clear()
        interval = max(1, int(settings.live_scan_interval_seconds))
        thread = threading.Thread(
            target=self._scan_runner, args=(interval,), name="live-scan", daemon=True
        )
        self._scan_thread = thread
        self._state.live_scan_running = True
        thread.start()

    def _scan_runner(self, interval: int) -> None:
        # stop flag가 설정될 때까지 interval마다 1 cycle. 첫 cycle은 start()에서 이미 동기 실행됨.
        while not self._scan_stop.wait(interval):
            if not self._state.automation_running:
                break
            self._run_one_cycle()

    def _run_one_cycle(self) -> None:
        if self._scan_loop is None:
            return
        with self._scan_lock:
            try:
                events = self._scan_loop.scan_cycle(
                    session_id=self._state.session_id,
                    trading_mode=self._state.trading_mode,
                )
            except Exception:  # noqa: BLE001 - 스캔 실패가 세션을 죽이지 않게(graceful)
                return
        self._state.last_scan_at = _now_iso()
        self._state.last_scan_event_count = len(events)
        self._state.latest_buy_candidates = [e.symbol for e in events if e.buy_candidate]

    def _stop_scan(self) -> None:
        self._scan_stop.set()
        thread = self._scan_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._scan_thread = None
        self._state.live_scan_running = False

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
