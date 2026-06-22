"""MockLLMReviewProvider 테스트 (spec: specs/live_decision_pipeline.md).

무비용(cost 0.00)·결정론·외부 API/키 없음. ERROR/INSUFFICIENT_DATA veto, 비-BUY veto,
피처 불완전 needs_review, 강한 BUY approve. 알 수 없는 provider fail-closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.services.live_scan import ScanEvent
from backend.app.services.llm_review import (
    MOCK_PROVIDER_NAME,
    LLMProviderNotConfigured,
    MockLLMReviewProvider,
    get_llm_review_provider,
)


def _event(status="BUY_CANDIDATE", *, features=None, symbol="NVDA", price=100.0):
    full = {"trend": "UP", "relative_strength": True, "rsi": 55.0, "regime": "NORMAL_BULL", "price": price}
    return ScanEvent(
        timestamp="2026-06-22T10:00:00+00:00",
        session_id="s1",
        symbol=symbol,
        price=price,
        scan_status=status,
        reason="",
        features=full if features is None else features,
        buy_candidate=(status == "BUY_CANDIDATE"),
    )


def test_default_provider_is_mock():
    provider = get_llm_review_provider(Settings())
    assert provider.name == MOCK_PROVIDER_NAME


def test_unknown_provider_fails_closed():
    with pytest.raises(LLMProviderNotConfigured):
        get_llm_review_provider(Settings(llm_provider="gpt-4o"))


def test_mock_cost_is_zero():
    r = MockLLMReviewProvider().review(_event())
    assert r.cost_usd == 0.0
    assert r.provider_name == MOCK_PROVIDER_NAME


def test_mock_is_deterministic():
    a = MockLLMReviewProvider().review(_event())
    b = MockLLMReviewProvider().review(_event())
    assert (a.decision, a.confidence, a.reason) == (b.decision, b.confidence, b.reason)


def test_approve_strong_buy_candidate():
    r = MockLLMReviewProvider().review(_event())
    assert r.decision == "approve"
    assert r.confidence > 0.5
    # 리스크 상향 불가: override는 None(또는 cap 이하). 여기서는 None.
    assert r.max_notional_override_usd is None


@pytest.mark.parametrize("status", ["ERROR", "INSUFFICIENT_DATA"])
def test_veto_on_bad_status(status):
    r = MockLLMReviewProvider().review(_event(status))
    assert r.decision == "veto"


@pytest.mark.parametrize("status", ["REJECT", "SKIP"])
def test_never_approve_non_buy_candidate(status):
    r = MockLLMReviewProvider().review(_event(status))
    assert r.decision == "veto"  # 비-BUY_CANDIDATE는 승인 금지


def test_needs_review_on_incomplete_features():
    incomplete = {"trend": "UP", "relative_strength": None, "rsi": None, "regime": "NORMAL_BULL"}
    r = MockLLMReviewProvider().review(_event(features=incomplete))
    assert r.decision == "needs_review"


def test_no_external_api_or_key_imports():
    # 정적 가드: mock 리뷰 경로는 외부 LLM/브로커/HTTP/키 모듈을 import하지 않는다(import 라인만 검사).
    import backend.app.services.llm_review as mod

    lines = Path(mod.__file__).read_text(encoding="utf-8").lower().splitlines()
    imports = "\n".join(x for x in lines if x.strip().startswith(("import ", "from ")))
    for forbidden in ("openai", "anthropic", "claude", "requests", "httpx", "robinhood", "dotenv"):
        assert forbidden not in imports, f"mock LLM 경로가 {forbidden}를 import함"
