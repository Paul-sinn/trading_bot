"""Mock LLM 리뷰 provider 경계 — BUY_CANDIDATE 정성 판단(무비용, 결정론).

CRITICAL: **실 LLM API 호출 없음, API 키 읽기 없음, 비용 0.00.** mock provider는 외부 네트워크를
건드리지 않고 scan_event의 status/features만으로 결정론적 판단을 만든다. LLM은 RiskGate/
ExecutionGate를 우회·무력화할 수 없으며(자문일 뿐), 리스크를 상향(노셔널 증액)시킬 수 없다.

`LLM_PROVIDER=mock` 기본. 실 LLM provider가 아직 없으므로 그 외 값은 fail-closed(예외).

spec: specs/live_decision_pipeline.md
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.services.live_scan import BUY_CANDIDATE, ScanEvent

ReviewDecision = Literal["approve", "veto", "needs_review"]

MOCK_PROVIDER_NAME = "mock_llm"


class LLMProviderNotConfigured(RuntimeError):
    """알 수 없는/미설정 LLM provider — fail-closed(실 LLM 경로 없음)."""


class ReviewResult(BaseModel):
    """mock LLM 리뷰 결과. cost_usd는 불변식상 항상 0.0(무비용)."""

    symbol: str
    decision: ReviewDecision
    confidence: float
    reason: str
    risk_notes: str = ""
    can_reduce_notional: bool = True
    # 있다면 cap 이하만 허용(리스크 상향 금지). None이면 cap 그대로.
    max_notional_override_usd: float | None = None
    cost_usd: float = 0.0
    provider_name: str = MOCK_PROVIDER_NAME


@runtime_checkable
class LLMReviewProvider(Protocol):
    """LLM 리뷰 인터페이스. 구현은 mock(현재) / 실 provider(추후)로 분기."""

    name: str

    def review(self, scan_event: ScanEvent) -> ReviewResult: ...


def _features_complete(features: dict) -> bool:
    """리뷰에 필요한 핵심 피처가 모두 채워졌는지(누락 → needs_review)."""
    for key in ("trend", "relative_strength", "rsi", "regime"):
        if features.get(key) is None:
            return False
    return True


class MockLLMReviewProvider:
    """결정론 mock 리뷰어(네트워크·키·비용 0). scan_event status/features만 사용.

    규칙: ERROR/INSUFFICIENT_DATA → veto · 비-BUY_CANDIDATE → veto(승인 금지) ·
    피처 불완전 → needs_review · 그 외 강한 BUY → approve. confidence는 피처에서 결정론 파생.
    """

    name = MOCK_PROVIDER_NAME

    def review(self, scan_event: ScanEvent) -> ReviewResult:
        status = scan_event.scan_status
        symbol = scan_event.symbol
        features = scan_event.features or {}

        if status in ("ERROR", "INSUFFICIENT_DATA"):
            return self._result(symbol, "veto", 0.1, f"스캔 상태 {status} — 데이터 신뢰 불가",
                                 risk_notes="데이터 품질 미달")
        if status != BUY_CANDIDATE:
            # 비-BUY_CANDIDATE는 절대 승인하지 않는다.
            return self._result(symbol, "veto", 0.1, f"BUY_CANDIDATE 아님(status={status})",
                                 risk_notes="진입 시그널 없음")
        if not _features_complete(features):
            return self._result(symbol, "needs_review", 0.5, "피처 불완전 — 사람 검토 필요",
                                 risk_notes="trend/상대강도/rsi/regime 일부 누락")

        # 강한 BUY_CANDIDATE: 상대강도 True + 추세 UP이면 고확신 approve.
        rs = features.get("relative_strength") is True
        trend_up = features.get("trend") == "UP"
        confidence = round(0.7 + (0.15 if rs else 0.0) + (0.1 if trend_up else 0.0), 2)
        return self._result(
            symbol, "approve", confidence,
            "BUY_CANDIDATE 승인: 추세/상대강도 합치(눌림목 재개)",
            risk_notes="잠긴 베이스라인 리스크 한도 내(stop 0.15/trail 0.20).",
        )

    def _result(
        self,
        symbol: str,
        decision: ReviewDecision,
        confidence: float,
        reason: str,
        *,
        risk_notes: str = "",
    ) -> ReviewResult:
        # cost_usd는 항상 0.0(무비용). override는 None(리스크 상향 불가).
        return ReviewResult(
            symbol=symbol,
            decision=decision,
            confidence=confidence,
            reason=reason,
            risk_notes=risk_notes,
            cost_usd=0.0,
            provider_name=self.name,
        )


def get_llm_review_provider(settings: Settings | None = None) -> LLMReviewProvider:
    """`LLM_PROVIDER` 기반 리뷰어 선택. mock만 구현 — 그 외는 fail-closed(실 LLM 경로 없음)."""
    settings = settings or Settings()
    name = (settings.llm_provider or "").strip().lower()
    if name == "mock":
        return MockLLMReviewProvider()
    raise LLMProviderNotConfigured(
        f"알 수 없는 LLM_PROVIDER={settings.llm_provider!r} (현재 'mock'만 지원 — 실 LLM API 경로 없음)"
    )
