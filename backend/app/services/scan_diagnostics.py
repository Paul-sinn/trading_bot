"""라이브 스캔 진단 v1 — 각 종목이 왜 BUY_CANDIDATE/SKIP인지 비전문가도 이해하게 설명한다.

**진단 전용**: 기존 ScanEvent(읽기)를 사람 친화 설명으로 변환만 한다. 매매 규칙 변경 없음,
주문/승인/Robinhood write/Alpaca 거래 없음. 기술 필드(technical_reason 등)는 'Advanced'로 분리.

spec: specs/real_order_v1_checklist.md §19
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.services.live_scan import (
    BUY_CANDIDATE,
    ERROR,
    ScanEvent,
    load_scan_events,
)
from backend.app.services.live_universe_policy import evaluate_symbol_policy, user_policy_label, get_live_universe_entry

_BEARISH_REGIMES = {"BEARISH", "PANIC", "spy_bear_vix_unknown"}
_BULLISH_REGIMES = {"NORMAL_BULL", "NERVOUS_BULL", "spy_bull_vix_unknown"}


# --- 모델 ---
class SymbolDiagnostic(BaseModel):
    symbol: str
    final_decision: str  # BUY_CANDIDATE | SKIPPED | ERROR
    human_reason: str  # 사람 친화(기본 노출)
    technical_reason: str  # 내부 reason/code(Advanced)
    price: float | None = None
    trend_status: str = "-"
    momentum_status: str = "-"
    pullback_status: str = "-"
    volume_status: str = "정보 없음 (이 단계 미평가)"
    regime_status: str = "-"
    data_status: str = "정상"
    signal_strength: str = "약함"
    confidence: float | None = None
    timestamp: str | None = None
    # Advanced 원자료.
    scan_status: str | None = None
    regime: str | None = None
    regime_source: str | None = None
    policy_tier: str | None = None
    policy_status: str = "unknown"
    policy_label: str = "정책 없음: 자동매수 차단"
    policy_reason: str = "정책에 없는 종목이라 자동매수를 차단했습니다."
    policy_decision: str = "blocked_unknown_ticker"
    policy_tradable: bool = False
    approval_allowed: bool = False


class ClosestCandidate(BaseModel):
    symbol: str
    signal_strength: str
    reason: str


class ScanDiagnosticsSummary(BaseModel):
    total_scanned: int = 0
    buy_candidates: int = 0
    skipped: int = 0
    errors: int = 0
    market_condition: str = "시장 상태: 판단 불가"
    regime: str | None = None
    regime_source: str | None = None
    vix_value: float | None = None
    risk_reduced: bool = False
    vix_warning: str | None = None
    main_skip_reason: str | None = None
    top_closest: list[ClosestCandidate] = Field(default_factory=list)
    headline: str = ""
    as_of: str | None = None


class ScanDiagnosticsView(BaseModel):
    summary: ScanDiagnosticsSummary
    symbols: list[SymbolDiagnostic] = Field(default_factory=list)


# --- 매핑 헬퍼 ---
def _trend_status(trend: str | None) -> str:
    return {"UP": "상승 추세", "DOWN": "하락 추세", "NEUTRAL": "추세 불명확"}.get(trend or "", "추세 정보 없음")


def _momentum_status(rs) -> str:
    if rs is True:
        return "SPY 대비 강함"
    if rs is False:
        return "SPY 대비 약함"
    return "정보 없음"


def _regime_status(regime: str | None) -> str:
    return {
        "NORMAL_BULL": "매수 가능 구간",
        "NERVOUS_BULL": "조심스러운 매수 구간",
        "spy_bull_vix_unknown": "매수 가능(VIX 불명, 보수적)",
        "BEARISH": "약세 — 신규 매수 제한",
        "spy_bear_vix_unknown": "약세(VIX 불명) — 신규 매수 제한",
        "PANIC": "위험 — 매수 중단",
        "insufficient_spy": "판단 불가(SPY 데이터 부족)",
    }.get(regime or "", "정보 없음")


def _pullback_status(scan_status: str, reason: str) -> str:
    if scan_status == BUY_CANDIDATE:
        return "눌림목 후 재개 신호 발생 (진입)"
    if "눌림 없음" in reason:
        return "아직 눌림목(되돌림)이 없음 — 대기"
    if "재개 신호 없음" in reason:
        return "눌림 후 재개 신호 대기 중"
    if "게이트 실패" in reason:
        return "진입 게이트 미통과(추세/상대강도/레짐)"
    if "데이터" in reason or "워밍업" in reason:
        return "데이터 부족 — 평가 보류"
    return "-"


def _human_reason(scan_status: str, reason: str, regime: str | None, trend: str | None) -> str:
    if scan_status == BUY_CANDIDATE:
        return "상승 추세에서 눌림목 후 재개 신호가 나와 매수 후보로 선정되었습니다."
    if scan_status == ERROR:
        return "데이터 조회 오류로 이 종목은 판단하지 못했습니다."
    if "데이터" in reason or "워밍업" in reason or scan_status == "INSUFFICIENT_DATA":
        return "가격 데이터가 부족해서 판단하지 않았습니다."
    if (regime in _BEARISH_REGIMES) or "신규 진입 불가" in reason:
        return "시장 위험도가 높아 신규 매수를 막았습니다."
    if "상대강도" in reason:
        return "상승 흐름이 SPY(시장)보다 약해서 매수 대상이 아닙니다."
    if trend != "UP" or "상승추세 아님" in reason:
        return "상승 추세가 아니라 매수 대상이 아닙니다."
    return "상승 추세는 맞지만, 아직 매수 타이밍(눌림목·진입 조건)이 아닙니다."


def _closeness(ev: ScanEvent) -> int:
    """매수에 얼마나 근접했는지(0~3). 높을수록 근접. BUY는 별도 처리."""
    feats = ev.features if isinstance(ev.features, dict) else {}
    score = 0
    if feats.get("trend") == "UP":
        score += 1
    if feats.get("relative_strength") is True:
        score += 1
    if "트리거 미충족" in (ev.reason or ""):  # 게이트는 통과, 타이밍만 미충족 = 가장 근접
        score += 1
    return score


def _strength_label(ev: ScanEvent) -> str:
    if ev.scan_status == BUY_CANDIDATE:
        return "통과"
    score = _closeness(ev)
    return {3: "근접", 2: "보통"}.get(score, "약함")


def build_symbol_diagnostic(ev: ScanEvent) -> SymbolDiagnostic:
    feats = ev.features if isinstance(ev.features, dict) else {}
    reason = ev.reason or ""
    regime = feats.get("regime")
    trend = feats.get("trend")
    policy = evaluate_symbol_policy(ev.symbol, confidence=None)
    entry = get_live_universe_entry(ev.symbol)
    decision = "BUY_CANDIDATE" if ev.scan_status == BUY_CANDIDATE else ("ERROR" if ev.scan_status == ERROR else "SKIPPED")
    data_status = "데이터 부족/오류" if decision == "ERROR" or ev.scan_status == "INSUFFICIENT_DATA" else "정상"
    return SymbolDiagnostic(
        symbol=ev.symbol,
        final_decision=decision,
        human_reason=_human_reason(ev.scan_status, reason, regime, trend),
        technical_reason=reason or ev.scan_status,
        price=ev.price,
        trend_status=_trend_status(trend),
        momentum_status=_momentum_status(feats.get("relative_strength")),
        pullback_status=_pullback_status(ev.scan_status, reason),
        regime_status=_regime_status(regime),
        data_status=data_status,
        signal_strength=_strength_label(ev),
        timestamp=ev.timestamp,
        scan_status=ev.scan_status,
        regime=regime,
        regime_source=ev.regime_source,
        policy_tier=policy.tier,
        policy_status=policy.status,
        policy_label=user_policy_label(entry),
        policy_reason=policy.user_reason,
        policy_decision=policy.decision,
        policy_tradable=policy.tradable,
        approval_allowed=policy.approval_allowed,
    )


def _latest_cycle(events: list[ScanEvent]) -> list[ScanEvent]:
    """가장 최근 1회 스캔 사이클의 이벤트만(뒤에서부터 심볼이 반복되기 직전까지)."""
    out: list[ScanEvent] = []
    seen: set[str] = set()
    for ev in reversed(events):
        if ev.symbol in seen:
            break
        seen.add(ev.symbol)
        out.append(ev)
    return list(reversed(out))


def _market_condition(regime: str | None) -> str:
    if regime in _BULLISH_REGIMES:
        if regime in ("NERVOUS_BULL", "spy_bull_vix_unknown"):
            return "시장 상태: 조심스러운 매수 구간"
        return "시장 상태: 매수 가능 구간"
    if regime in _BEARISH_REGIMES:
        return "시장 상태: 약세 — 신규 매수 제한"
    return "시장 상태: 판단 불가 (SPY 데이터 부족)"


def build_summary(diags: list[SymbolDiagnostic], cycle: list[ScanEvent]) -> ScanDiagnosticsSummary:
    total = len(diags)
    buys = sum(1 for d in diags if d.final_decision == "BUY_CANDIDATE")
    errs = sum(1 for d in diags if d.final_decision == "ERROR")
    skipped = total - buys - errs
    ref = cycle[-1] if cycle else None
    regime = (ref.features.get("regime") if ref and isinstance(ref.features, dict) else None)
    market = _market_condition(regime)

    skip_reasons = [d.human_reason for d in diags if d.final_decision == "SKIPPED"]
    main_skip = Counter(skip_reasons).most_common(1)[0][0] if skip_reasons else None

    ranked = sorted(
        [(_closeness(ev), ev) for ev in cycle if ev.scan_status != BUY_CANDIDATE],
        key=lambda t: (-t[0], t[1].symbol),
    )
    top = [
        ClosestCandidate(
            symbol=ev.symbol, signal_strength=_strength_label(ev),
            reason=_human_reason(ev.scan_status, ev.reason or "", (ev.features or {}).get("regime"), (ev.features or {}).get("trend")),
        )
        for _, ev in ranked[:3]
    ]

    if total == 0:
        headline = "아직 스캔 기록이 없습니다. 대시보드에서 '거래 시작'(report_only)을 누르거나 스캔을 한 번 실행하세요."
    elif buys > 0:
        headline = f"오늘 {total}개 중 {buys}개 종목이 매수 조건을 통과했습니다. ({market})"
    elif errs == total or (ref is None):
        headline = "데이터가 부족하거나 오류라서 오늘 스캔을 신뢰하기 어렵습니다. 데이터 연결/설정을 확인하세요."
    elif regime in _BEARISH_REGIMES:
        headline = f"시장 위험도가 높아 오늘은 신규 매수를 막았습니다. ({total}개 종목 모두 신규 매수 제한)"
    else:
        headline = (
            f"오늘은 시장 상태는 괜찮지만, {total}개 종목 중 매수 타이밍을 통과한 종목이 없습니다. "
            "대부분 눌림목/진입(pullback/entry) 조건을 아직 만족하지 못했습니다."
        )

    return ScanDiagnosticsSummary(
        total_scanned=total, buy_candidates=buys, skipped=skipped, errors=errs,
        market_condition=market, regime=regime,
        regime_source=ref.regime_source if ref else None,
        vix_value=ref.vix_value if ref else None,
        risk_reduced=bool(ref.risk_reduced) if ref else False,
        vix_warning=ref.regime_warning if ref else None,
        main_skip_reason=main_skip, top_closest=top, headline=headline,
        as_of=ref.timestamp if ref else None,
    )


def latest_diagnostics(*, reports_dir: Path | None = None) -> ScanDiagnosticsView:
    """가장 최근 스캔 사이클의 진단(요약 + 종목별). 읽기 전용 — 스캔 시작/주문 없음."""
    events = load_scan_events(limit=500, reports_dir=reports_dir)
    cycle = _latest_cycle(events)
    diags = [build_symbol_diagnostic(ev) for ev in cycle]
    return ScanDiagnosticsView(summary=build_summary(diags, cycle), symbols=diags)


def recent_diagnostics(*, limit: int = 50, reports_dir: Path | None = None) -> list[SymbolDiagnostic]:
    """최근 스캔 이벤트 N개를 종목 진단으로(여러 사이클 가능). 읽기 전용."""
    return [build_symbol_diagnostic(ev) for ev in load_scan_events(limit=limit, reports_dir=reports_dir)]
