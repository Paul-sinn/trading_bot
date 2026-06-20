"""dry_run 검토용 판단 리포트 테스트 (spec: specs/dry_run.md).

핵심 불변식: orders_placed는 항상 0(BUY 판단이 나와도). RiskGate 최종권: veto면 BUY는 HOLD로 강등.
순수 조립만 검증 — 네트워크/주문/브로커 없음.
"""

import pytest

from agents.decision import Decision
from agents.dry_run import (
    DryRunDecision,
    DryRunReport,
    build_dry_run_decision,
    build_dry_run_report,
    format_dry_run_report,
)
from algorithms.policy import RiskMode, TierEntry, UniversePolicy, VetoInput
from algorithms.regime import Regime


def _mode_b() -> RiskMode:
    return RiskMode("B", 0.07, ("0", "1", "2", "3", "4A", "4B"), False, (), True)


def _universe() -> UniversePolicy:
    return UniversePolicy(
        entries=(
            TierEntry("NVDA", "1", ("1",), "approved", True, False),
            TierEntry("SMCI", "2", ("2",), "needs_review", True, False),
            TierEntry("DEADX", "2", ("2",), "reject", True, False),
        )
    )


def _clean_input(symbol="NVDA", **ov) -> VetoInput:
    base = dict(
        symbol=symbol, mode=_mode_b(), universe=_universe(),
        per_trade_risk_pct=0.04, position_weight=0.5, stop_loss_pct=0.08,
        regime=Regime.NORMAL_BULL, has_stop_loss=True, position_size_ok=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return VetoInput(**base)


# --- build_dry_run_decision ---


def test_clean_buy_passes_through():
    d = build_dry_run_decision(_clean_input(), Decision.BUY)
    assert d.veto.passed is True
    assert d.raw_decision is Decision.BUY
    assert d.effective_decision is Decision.BUY
    assert d.tier == "1" and d.status == "approved"


def test_vetoed_buy_downgraded_to_hold():
    # liquidity 실패 → veto → BUY가 HOLD로 강등(RiskGate 최종권).
    d = build_dry_run_decision(_clean_input(liquidity_ok=False), Decision.BUY)
    assert d.veto.passed is False
    assert d.raw_decision is Decision.BUY
    assert d.effective_decision is Decision.HOLD
    assert any("liquidity" in r for r in d.veto.reasons)


def test_needs_review_buy_downgraded():
    d = build_dry_run_decision(_clean_input(symbol="SMCI"), Decision.BUY)
    assert d.effective_decision is Decision.HOLD  # needs_review override 없음


def test_needs_review_buy_with_override_stays_buy():
    d = build_dry_run_decision(_clean_input(symbol="SMCI", manual_override=True), Decision.BUY)
    assert d.veto.passed is True
    assert d.effective_decision is Decision.BUY


def test_sell_not_downgraded_by_veto():
    # 청산(SELL)은 진입 veto로 강등하지 않는다.
    d = build_dry_run_decision(_clean_input(liquidity_ok=False), Decision.SELL)
    assert d.veto.passed is False
    assert d.effective_decision is Decision.SELL


def test_hold_stays_hold():
    d = build_dry_run_decision(_clean_input(), Decision.HOLD)
    assert d.effective_decision is Decision.HOLD


def test_unregistered_symbol_vetoed_and_none_tier():
    d = build_dry_run_decision(_clean_input(symbol="ZZZZ"), Decision.BUY)
    assert d.tier is None and d.status is None
    assert d.effective_decision is Decision.HOLD


def test_rationale_includes_veto_reasons():
    d = build_dry_run_decision(_clean_input(data_ok=False), Decision.BUY, rationale="모멘텀 강함")
    assert "모멘텀 강함" in d.rationale
    assert "결측" in d.rationale or "데이터" in d.rationale


# --- build_dry_run_report + orders_placed 불변식 ---


def _report_with(decisions):
    return build_dry_run_report(
        report_date="2026-06-19", account_phase="1", risk_mode="B",
        regime="NORMAL_BULL", compass_state="strong", decisions=decisions,
    )


def test_orders_placed_always_zero_even_with_buys():
    ds = (
        build_dry_run_decision(_clean_input("NVDA"), Decision.BUY),  # passes → effective BUY
        build_dry_run_decision(_clean_input("SMCI"), Decision.BUY),  # vetoed
    )
    report = _report_with(ds)
    assert report.orders_placed == 0           # BUY가 있어도 항상 0
    assert "NVDA" in report.review_buys
    assert report.riskgate_vetoes == 1
    assert report.mdd_hard_stop_pct == 0.20
    assert report.no_return_guarantee is True


def test_empty_report():
    report = _report_with(())
    assert report.orders_placed == 0
    assert report.riskgate_vetoes == 0
    assert report.review_buys == ()


def test_report_is_frozen():
    report = _report_with(())
    assert isinstance(report, DryRunReport)
    with pytest.raises(Exception):
        report.report_date = "x"  # type: ignore[misc]


def test_format_report_has_invariants_and_dryrun_marker():
    ds = (build_dry_run_decision(_clean_input("NVDA"), Decision.BUY),)
    text = format_dry_run_report(_report_with(ds))
    assert "DRY-RUN" in text
    assert "orders_placed" in text and "0" in text
    assert "0.20" in text
    assert "NVDA" in text
