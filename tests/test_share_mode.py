"""분수주(fractional share) 시뮬 모드 테스트 (spec: specs/share_mode.md).

소액 계좌가 고가주를 분수주로 시뮬 매수. 기존 정수주 동작은 기본값 그대로. 리스크 캡 동일 적용,
veto된 후보는 주문/체결 0. real_orders_placed=0. 실브로커/네트워크 없음.
"""

import math
import sys
from pathlib import Path

import pytest

from agents.decision import Decision
from agents.sim_execution import SimulatedExecutor, SimulatedOrder
from algorithms.policy import RiskMode, TierEntry, UniversePolicy, VetoInput
from algorithms.regime import Regime
from algorithms.sizing import (
    PositionPlan,
    ShareMode,
    per_trade_risk_pct,
    position_size,
)

# 소액 계좌 + 고가주 시나리오.
_EQUITY = 1000.0
_ENTRY = 500.0          # 고가주($500): $1,000으로 1주도 못 산다.
_STOP = 460.0           # per-share risk = $40
_MAX_RISK = 0.05
_KELLY = 0.25
_APPETITE = 1.0


def _size(mode, **ov):
    kw = dict(
        account_equity=_EQUITY, entry_price=_ENTRY, stop_loss_price=_STOP,
        max_risk_pct=_MAX_RISK, kelly_f=_KELLY, appetite_weight=_APPETITE,
    )
    kw.update(ov)
    return position_size(**kw, share_mode=mode)


# --- WHOLE 기본 동작 불변 ---


def test_default_is_whole_and_unchanged():
    default = position_size(_EQUITY, _ENTRY, _STOP, _MAX_RISK, _KELLY, _APPETITE)
    explicit = _size(ShareMode.WHOLE)
    assert default.quantity == explicit.quantity
    assert isinstance(default.quantity, int)          # 정수주 계약 유지


def test_whole_mode_zero_for_small_account_highprice():
    # $1,000 / $500주 정수주 → 0주(문제 상황 문서화).
    assert _size(ShareMode.WHOLE).quantity == 0


# --- FRACTIONAL: 소액·고가주에서 분수 수량 ---


def test_fractional_nonzero_for_small_account_highprice():
    p = _size(ShareMode.FRACTIONAL)
    assert isinstance(p, PositionPlan)
    assert 0.0 < p.quantity < 1.0                     # 1주 미만 분수주


def test_fractional_respects_lot_precision():
    lot = 0.001
    p = _size(ShareMode.FRACTIONAL, **{})
    # 수량은 lot_size 배수.
    ratio = p.quantity / lot
    assert ratio == pytest.approx(round(ratio), abs=1e-6)


def test_fractional_respects_risk_caps_exactly():
    p = _size(ShareMode.FRACTIONAL)
    allowed_risk = _EQUITY * _MAX_RISK
    # ADR-003: risk_amount는 허용 리스크 초과 금지(분수 단위 동일).
    assert p.risk_amount <= allowed_risk + 1e-9
    # 불변식①: per_trade_risk_pct <= max_risk_pct.
    assert per_trade_risk_pct(p.risk_amount, _EQUITY) <= _MAX_RISK + 1e-9


def test_fractional_notional_within_account():
    p = _size(ShareMode.FRACTIONAL)
    notional = p.quantity * _ENTRY
    assert notional <= _EQUITY + 1e-9                 # 계좌 넘는 분수주 없음


# --- fail-closed (오설정) ---


def test_bad_lot_size_fails_closed():
    with pytest.raises(ValueError):
        _size(ShareMode.FRACTIONAL, lot_size=0.0)
    with pytest.raises(ValueError):
        _size(ShareMode.FRACTIONAL, lot_size=-0.01)


def test_unknown_share_mode_fails_closed():
    with pytest.raises(ValueError):
        position_size(_EQUITY, _ENTRY, _STOP, _MAX_RISK, _KELLY, _APPETITE, share_mode="bogus")


# --- veto된 후보는 분수 수량이어도 주문/체결 0 ---


def _mode_b() -> RiskMode:
    return RiskMode("B", 0.07, ("0", "1", "2", "3", "4A", "4B"), False, (), True)


def _universe() -> UniversePolicy:
    return UniversePolicy(entries=(TierEntry("NVDA", "1", ("1",), "approved", True, False),))


def _veto_input(**ov) -> VetoInput:
    base = dict(
        symbol="NVDA", mode=_mode_b(), universe=_universe(),
        per_trade_risk_pct=0.04, position_weight=0.5, stop_loss_pct=0.08,
        regime=Regime.NORMAL_BULL, has_stop_loss=True, position_size_ok=True,
        liquidity_ok=True, tier_exposure_ok=True, data_ok=True, ipo_data_ok=True,
        event_risk_checked=True, technical_confirmation=True, manual_override=False,
    )
    base.update(ov)
    return VetoInput(**base)


def test_vetoed_fractional_candidate_creates_no_order():
    ex = SimulatedExecutor(global_gate=lambda: (True, "ok"))
    # liquidity 실패 → veto. 분수 수량(0.5)이어도 주문/체결 없어야.
    res = ex.submit(_veto_input(liquidity_ok=False), Decision.BUY, 0.5)
    assert res.created is False
    assert res.order is None
    assert ex.simulated_orders == ()
    assert ex.real_orders_placed == 0


def test_passing_fractional_candidate_creates_fractional_order():
    ex = SimulatedExecutor(global_gate=lambda: (True, "ok"))
    res = ex.submit(_veto_input(), Decision.BUY, 0.25)
    assert res.created is True
    assert isinstance(res.order, SimulatedOrder)
    assert res.order.quantity == 0.25                 # 분수 수량 그대로
    assert ex.real_orders_placed == 0


# --- CLI 플래그 배선 ---


def test_run_sim_accepts_share_mode_flag():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_sim  # noqa: E402

    parser = run_sim.build_arg_parser()
    args = parser.parse_args(["--data-root", "data/x", "--share-mode", "fractional"])
    assert args.share_mode == "fractional"
    # 기본은 whole(기존 동작 유지).
    default_args = parser.parse_args(["--data-root", "data/x"])
    assert default_args.share_mode == "whole"
