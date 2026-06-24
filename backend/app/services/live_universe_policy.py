"""Live universe tier policy v1.

Read-only market-data/router policy. This module never creates approvals, submits orders,
or talks to broker/trading APIs.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = _REPO_ROOT / "config" / "live_universe_tiers.json"

UniverseStatus = Literal["approved", "watch", "needs_review", "compass_only"]


class LiveUniverseEntry(BaseModel):
    ticker: str
    tier: str
    status: UniverseStatus
    tradable: bool
    approval_allowed: bool
    role: str
    category: str
    reason: str
    risk_multiplier: float
    secondary_tiers: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def _upper_ticker(cls, value: str) -> str:
        return value.strip().upper()


class LiveUniversePolicy(BaseModel):
    tickers: list[LiveUniverseEntry]

    @property
    def by_ticker(self) -> dict[str, LiveUniverseEntry]:
        out: dict[str, LiveUniverseEntry] = {}
        for entry in self.tickers:
            if entry.ticker in out:
                raise ValueError(f"duplicate live universe ticker: {entry.ticker}")
            out[entry.ticker] = entry
        return out


class UniversePolicyDecision(BaseModel):
    ticker: str
    allowed: bool
    decision: str
    user_reason: str
    technical_reason: str
    tier: str | None = None
    status: str = "unknown"
    tradable: bool = False
    approval_allowed: bool = False
    role: str | None = None
    category: str | None = None
    risk_multiplier: float = 0.0
    secondary_tiers: list[str] = Field(default_factory=list)


def normalize_ticker(symbol: str) -> str:
    return symbol.strip().upper()


@lru_cache(maxsize=1)
def load_live_universe_policy(path: str | None = None) -> LiveUniversePolicy:
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    raw = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = LiveUniversePolicy.model_validate(raw)
    _ = policy.by_ticker  # duplicate validation
    return policy


def get_live_universe_entry(symbol: str) -> LiveUniverseEntry | None:
    return load_live_universe_policy().by_ticker.get(normalize_ticker(symbol))


def user_policy_label(entry: LiveUniverseEntry | None) -> str:
    if entry is None:
        return "정책 없음: 자동매수 차단"
    if entry.status == "compass_only" or entry.tier == "0":
        return "레짐 확인용: 매매 안 함"
    if entry.status == "watch":
        return "관찰만: 자동매수 금지"
    if entry.status == "needs_review":
        return "검토 필요: 자동매수 차단"
    if entry.tradable and entry.approval_allowed:
        return "실전 매수 허용"
    return "승인 목록이지만 자동매수 비활성"


def evaluate_symbol_policy(symbol: str, *, confidence: float | None = None) -> UniversePolicyDecision:
    ticker = normalize_ticker(symbol)
    entry = get_live_universe_entry(ticker)
    if entry is None:
        return UniversePolicyDecision(
            ticker=ticker,
            allowed=False,
            decision="blocked_unknown_ticker",
            user_reason="정책에 없는 종목이라 자동매수를 차단했습니다.",
            technical_reason="live_universe_policy:unknown_ticker",
        )

    label = user_policy_label(entry)
    base = dict(
        ticker=ticker,
        tier=entry.tier,
        status=entry.status,
        tradable=entry.tradable,
        approval_allowed=entry.approval_allowed,
        role=entry.role,
        category=entry.category,
        risk_multiplier=entry.risk_multiplier,
        secondary_tiers=entry.secondary_tiers,
    )

    if entry.tier == "0" or entry.status == "compass_only":
        return UniversePolicyDecision(
            **base,
            allowed=False,
            decision="blocked_compass_only",
            user_reason=label,
            technical_reason="live_universe_policy:compass_only",
        )
    if entry.status == "watch":
        return UniversePolicyDecision(
            **base,
            allowed=False,
            decision="blocked_watch_only",
            user_reason=label,
            technical_reason="live_universe_policy:watch",
        )
    if entry.status == "needs_review":
        return UniversePolicyDecision(
            **base,
            allowed=False,
            decision="blocked_needs_review",
            user_reason=label,
            technical_reason="live_universe_policy:needs_review",
        )
    if not entry.tradable or not entry.approval_allowed:
        return UniversePolicyDecision(
            **base,
            allowed=False,
            decision="blocked_not_live_enabled",
            user_reason=entry.reason or label,
            technical_reason="live_universe_policy:not_live_enabled",
        )
    if entry.tier == "2" and (confidence or 0.0) < 0.85:
        return UniversePolicyDecision(
            **base,
            allowed=False,
            decision="blocked_tier2_confidence",
            user_reason="Tier 2 종목은 더 강한 신호가 필요해서 자동매수를 보류했습니다.",
            technical_reason="live_universe_policy:tier2_confidence_below_0.85",
        )
    return UniversePolicyDecision(
        **base,
        allowed=True,
        decision="allowed",
        user_reason=entry.reason or label,
        technical_reason="live_universe_policy:allowed",
    )


def policy_score_bonus(symbol: str) -> float:
    entry = get_live_universe_entry(symbol)
    if entry is None:
        return -1.0
    if entry.tier == "1":
        return 0.2
    if entry.tier == "2":
        return 0.0
    return -0.2
