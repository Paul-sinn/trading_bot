"""Candidate 큐 + AI 예산/쿨다운 + mock 의사결정 오케스트레이션 (무비용, dry-run).

라이브 스캔의 BUY_CANDIDATE → CandidateQueue → MockLLMReviewProvider → ExecutionGate(dry-run) →
OrderIntent. **실 LLM API·브로커·Robinhood·실주문 없음.** mock 리뷰는 ai_calls_today에 카운트되지만
비용 0.00. 산출물은 reports/live_candidates.jsonl · reports/live_order_intents.jsonl 전용
(shadow 파일·Norgate 무관).

spec: specs/live_decision_pipeline.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.broker_snapshot import BrokerSnapshot, latest_snapshot
from backend.app.services.execution_gate import (
    ExecutionCaps,
    ExecutionGate,
    OrderIntent,
)
from backend.app.services.live_scan import BUY_CANDIDATE, ScanEvent
from backend.app.services.llm_review import (
    LLMReviewProvider,
    ReviewResult,
    get_llm_review_provider,
)

CandidateStatus = Literal[
    "queued", "reviewed", "vetoed", "approved", "needs_review", "blocked_by_execution_gate"
]
BlockReason = Literal["AI_BUDGET_EXCEEDED", "LLM_COOLDOWN_ACTIVE"]

CANDIDATES_LOG = "live_candidates.jsonl"
ORDER_INTENTS_LOG = "live_order_intents.jsonl"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class Candidate(BaseModel):
    """리뷰 대상 후보. real_orders_placed 없음(주문 아님)."""

    key: str
    scan_event_key: str
    session_id: str | None
    symbol: str
    date: str
    strategy_id: str
    price: float | None = None
    status: CandidateStatus = "queued"
    review: ReviewResult | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    block_reason: BlockReason | None = None
    created_at: str = ""
    reviewed_at: str | None = None


class AiStatus(BaseModel):
    """AI 예산/쿨다운 셸 상태(무비용)."""

    llm_provider: str
    ai_calls_today: int
    ai_cost_estimate_today: float = 0.0
    ai_budget_remaining: int = 0
    max_llm_calls_per_day: int = 0
    max_llm_cost_usd_per_day: float = 0.0
    cooldown_seconds_per_symbol: int = 0
    llm_budget_status: str = "OK"
    latest_review_at: str | None = None
    last_review_by_symbol: dict[str, str] = Field(default_factory=dict)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path, model: type[BaseModel], limit: int) -> list:
    limit = max(1, min(int(limit), 500))
    if not path.exists():
        return []
    out: list = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(model.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


class CandidatePipeline:
    """BUY_CANDIDATE를 mock 리뷰 + ExecutionGate(dry-run)로 처리. 주문/LLM 비용 없음."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        reports_dir: Path | None = None,
        review_provider: LLMReviewProvider | None = None,
        execution_gate: ExecutionGate | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._reports_dir = reports_dir
        self._review = review_provider or get_llm_review_provider(self._settings)
        self._gate = execution_gate or ExecutionGate()

        self._seen_keys: set[str] = set()
        self._intent_keys: set[str] = set()
        self._last_review_by_symbol: dict[str, datetime] = {}
        self._ai_calls_today = 0
        self._ai_day = _now().date().isoformat()
        self._daily_intent_count = 0
        self._total_intended_exposure = 0.0
        self._latest_review_at: str | None = None

    # --- 경로 헬퍼 ---
    def _candidates_path(self) -> Path:
        return (self._reports_dir or DEFAULT_REPORTS_DIR) / CANDIDATES_LOG

    def _intents_path(self) -> Path:
        return (self._reports_dir or DEFAULT_REPORTS_DIR) / ORDER_INTENTS_LOG

    def _caps(self) -> ExecutionCaps:
        s = self._settings
        return ExecutionCaps(
            max_notional_per_order_usd=s.max_notional_per_order_usd,
            max_daily_order_intents=s.max_daily_order_intents,
            max_total_intended_exposure_usd=s.max_total_intended_exposure_usd,
        )

    def _broker_snapshot(self) -> BrokerSnapshot | None:
        """ExecutionGate dry-run에 쓸 최신 브로커 스냅샷(읽기 전용 — MCP 호출 없음)."""
        return latest_snapshot(reports_dir=self._reports_dir)

    def _maybe_reset_day(self) -> None:
        today = _now().date().isoformat()
        if today != self._ai_day:
            self._ai_day = today
            self._ai_calls_today = 0
            self._daily_intent_count = 0
            self._total_intended_exposure = 0.0
            self._last_review_by_symbol.clear()

    # --- 메인 처리 ---
    def process_scan_events(
        self,
        events: list[ScanEvent],
        *,
        session_id: str | None,
        trading_mode: str,
        automation_running: bool,
        emergency_halt: bool,
    ) -> list[Candidate]:
        """스캔 이벤트를 후보로 처리. 정지/비상정지 시 처리 차단(빈 리스트)."""
        if not automation_running or emergency_halt:
            return []  # Stop/Emergency-Halt가 candidate 처리를 차단
        self._maybe_reset_day()

        strategy_id = self._settings.live_strategy_id
        produced: list[Candidate] = []
        for ev in events:
            if ev.scan_status != BUY_CANDIDATE:
                continue  # BUY_CANDIDATE만 처리
            date = (ev.timestamp or _now_iso())[:10]
            key = f"{session_id}|{ev.symbol}|{date}|{strategy_id}"
            if key in self._seen_keys:
                continue  # 멱등 dedupe
            self._seen_keys.add(key)

            cand = Candidate(
                key=key, scan_event_key=key, session_id=session_id, symbol=ev.symbol,
                date=date, strategy_id=strategy_id, price=ev.price, created_at=_now_iso(),
            )
            self._process_candidate(cand, ev, trading_mode, automation_running, emergency_halt)
            _append_jsonl(self._candidates_path(), cand.model_dump())
            produced.append(cand)
        return produced

    def _process_candidate(
        self,
        cand: Candidate,
        ev: ScanEvent,
        trading_mode: str,
        automation_running: bool,
        emergency_halt: bool,
    ) -> None:
        # 쿨다운: 심볼당 최소 간격 내 재리뷰 차단(LLM 호출 안 함).
        cooldown = self._settings.min_llm_cooldown_seconds_per_symbol
        last = self._last_review_by_symbol.get(cand.symbol)
        if last is not None and (_now() - last).total_seconds() < cooldown:
            cand.block_reason = "LLM_COOLDOWN_ACTIVE"
            return
        # AI 예산: 콜 한도 초과 시 차단(리뷰 안 함).
        if self._ai_calls_today >= self._settings.max_llm_calls_per_day:
            cand.block_reason = "AI_BUDGET_EXCEEDED"
            return

        # mock 리뷰(무비용, 결정론). ai_calls_today 카운트.
        review = self._review.review(ev)
        self._ai_calls_today += 1
        self._last_review_by_symbol[cand.symbol] = _now()
        self._latest_review_at = _now_iso()
        cand.review = review
        cand.reviewed_at = _now_iso()

        if review.decision == "veto":
            cand.status = "vetoed"
            return
        if review.decision == "needs_review":
            cand.status = "needs_review"
            return

        # approve → ExecutionGate(dry-run).
        cand.status = "reviewed"
        result, intent = self._gate.evaluate(
            symbol=cand.symbol,
            price=cand.price,
            review=review,
            source_status=ev.scan_status,
            scan_event_key=cand.scan_event_key,
            session_id=cand.session_id,
            trading_mode=trading_mode,
            strategy_id=cand.strategy_id,
            scan_run_id=cand.session_id,
            trading_date=cand.date,
            intent_generated_at=cand.reviewed_at,
            universe=_baseline_universe(),
            existing_intent_keys=self._intent_keys,
            daily_intent_count=self._daily_intent_count,
            total_intended_exposure_usd=self._total_intended_exposure,
            caps=self._caps(),
            automation_running=automation_running,
            emergency_halt=emergency_halt,
            broker_snapshot=self._broker_snapshot(),
            snapshot_max_age_seconds=self._settings.broker_snapshot_max_age_seconds,
            reject_on_stale_snapshot=self._settings.reject_on_stale_snapshot,
        )
        if result.status == "accepted_dry_run":
            cand.status = "approved"
            self._intent_keys.add(cand.scan_event_key)
            self._daily_intent_count += 1
            self._total_intended_exposure += intent.planned_notional_usd or 0.0
            _append_jsonl(self._intents_path(), intent.model_dump())
        else:
            cand.status = "blocked_by_execution_gate"
            cand.rejection_reasons = result.rejection_reasons

    # --- 읽기 전용 조회(주문/LLM 호출 없음) ---
    def candidates(self, limit: int = 50) -> list[Candidate]:
        return _load_jsonl(self._candidates_path(), Candidate, limit)

    def order_intents(self, limit: int = 50) -> list[OrderIntent]:
        return _load_jsonl(self._intents_path(), OrderIntent, limit)

    def ai_status(self) -> AiStatus:
        max_calls = self._settings.max_llm_calls_per_day
        remaining = max(0, max_calls - self._ai_calls_today)
        status = "AI_BUDGET_EXCEEDED" if remaining <= 0 else "OK"
        return AiStatus(
            llm_provider=self._settings.llm_provider,
            ai_calls_today=self._ai_calls_today,
            ai_cost_estimate_today=0.0,  # 불변식: mock 무비용
            ai_budget_remaining=remaining,
            max_llm_calls_per_day=max_calls,
            max_llm_cost_usd_per_day=self._settings.max_llm_cost_usd_per_day,
            cooldown_seconds_per_symbol=self._settings.min_llm_cooldown_seconds_per_symbol,
            llm_budget_status=status,
            latest_review_at=self._latest_review_at,
            last_review_by_symbol={s: t.isoformat() for s, t in self._last_review_by_symbol.items()},
        )


def _baseline_universe() -> tuple[str, ...]:
    # 지연 import로 순환 방지(live_scan은 이미 import됨 — 안전).
    from backend.app.services.live_scan import LIVE_BASELINE_UNIVERSE

    return LIVE_BASELINE_UNIVERSE
