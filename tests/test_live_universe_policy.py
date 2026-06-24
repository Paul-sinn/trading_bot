"""Live universe tier policy v1 tests — policy/read-only only."""

from __future__ import annotations

import inspect

from backend.app.services import live_universe_policy as lup
from backend.app.services.live_universe_policy import evaluate_symbol_policy, load_live_universe_policy


def test_config_has_no_duplicate_tickers():
    policy = load_live_universe_policy()
    tickers = [entry.ticker for entry in policy.tickers]
    assert len(tickers) == len(set(tickers))


def test_tier0_never_tradable():
    d = evaluate_symbol_policy("SPY", confidence=1.0)
    assert d.allowed is False
    assert d.tradable is False and d.approval_allowed is False
    assert d.decision == "blocked_compass_only"
    assert d.user_reason == "레짐 확인용: 매매 안 함"


def test_watch_and_needs_review_blocked():
    watch = evaluate_symbol_policy("ARM", confidence=1.0)
    review = evaluate_symbol_policy("SMCI", confidence=1.0)
    assert watch.allowed is False and watch.decision == "blocked_watch_only"
    assert watch.user_reason == "관찰만: 자동매수 금지"
    assert review.allowed is False and review.decision == "blocked_needs_review"
    assert review.user_reason == "검토 필요: 자동매수 차단"


def test_tier1_approved_allowed():
    d = evaluate_symbol_policy("NVDA", confidence=0.1)
    assert d.allowed is True
    assert d.tier == "1" and d.status == "approved"
    assert d.risk_multiplier == 1.0


def test_tier2_approved_requires_stricter_confidence():
    low = evaluate_symbol_policy("PLTR", confidence=0.84)
    high = evaluate_symbol_policy("PLTR", confidence=0.85)
    assert low.allowed is False and low.decision == "blocked_tier2_confidence"
    assert high.allowed is True and high.risk_multiplier == 0.75


def test_tier3_plus_approved_default_disabled():
    d = evaluate_symbol_policy("ETN", confidence=1.0)
    assert d.status == "approved"
    assert d.allowed is False and d.decision == "blocked_not_live_enabled"


def test_unknown_ticker_blocked_user_friendly():
    d = evaluate_symbol_policy("F", confidence=1.0)
    assert d.allowed is False
    assert d.decision == "blocked_unknown_ticker"
    assert "정책에 없는 종목" in d.user_reason


def test_multi_tag_tickers_have_single_primary_record():
    policy = load_live_universe_policy()
    coin = policy.by_ticker["COIN"]
    hood = policy.by_ticker["HOOD"]
    assert coin.tier == "2" and coin.secondary_tiers == ["6"]
    assert hood.tier == "2" and hood.secondary_tiers == ["6"]


def test_policy_module_has_no_order_side_effects():
    text = inspect.getsource(lup)
    assert "mcp__robinhood" not in text
    assert "place_equity_order" not in text
    assert "place_order" not in text
