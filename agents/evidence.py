"""CandidateContext 자동 구성 — 스캐너/데이터 출력에서 후보별 증거 + 사이징을 파생한다.

수동으로 증거 불리언을 채우지 않고 candidate(signal/filter) + OHLCV + 시장 데이터(spy/vix/benchmark)에서
파생한다. 데이터 조회/조립 I/O라 agents/에 둔다. scanner/algorithms 함수를 재사용만 한다 — 전략 시그널
로직/튜닝 변경 없음.

CRITICAL: 실브로커/Robinhood/라이브 없음. real orders=0. 슬리피지/체결 모델 없음.

CRITICAL (fail-closed): 증거를 만들 데이터가 없으면 해당 증거를 False/무효로 둔다(이후 hard-veto가 veto).
데이터 품질 불량이면 사이징을 무효(qty 0, stop 0, per_trade inf)로 만들어 candidate를 막는다.

spec: specs/evidence.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from agents.phase1_flow import CandidateContext
from algorithms.filters import _atr
from algorithms.regime import Regime, classify_regime
from algorithms.signals import Signal, relative_strength
from algorithms.sizing import (
    ShareMode,
    per_trade_risk_pct,
    position_size,
    stop_loss_pct,
    stop_loss_price,
)

_INF = float("inf")


# --- 이벤트 리스크 provider (외부 의존 주입) ---


@runtime_checkable
class EventRiskProvider(Protocol):
    """고임팩트 이벤트(earnings/FOMC/CPI) 확인 인터페이스. is_clear=True면 이벤트 리스크 점검됨/없음.

    as_of(선택)는 날짜 인지 provider(캘린더)용 — 그날 기준으로 확인한다. 무시해도 된다(Mock).
    """

    def is_clear(self, symbol: str, as_of=None) -> bool: ...


class MockEventRiskProvider:
    """결정론적 이벤트 provider. default=False = fail-closed(캘린더 없으면 미확인 → veto). as_of 무시."""

    def __init__(self, mapping: dict[str, bool] | None = None, default: bool = False) -> None:
        self._mapping = dict(mapping or {})
        self._default = default

    def is_clear(self, symbol: str, as_of=None) -> bool:
        return self._mapping.get(symbol, self._default)


@dataclass(frozen=True)
class EvidenceParams:
    """증거/사이징 파생 파라미터. 사이징 캡(max_risk_pct)은 ADR-003 하드캡 이내."""

    account_equity: float
    max_risk_pct: float = 0.02
    kelly_f: float = 0.25
    appetite_weight: float = 1.0
    atr_period: int = 14
    atr_multiplier: float = 2.0
    min_dollar_volume: float = 1e7
    rs_lookback: int = 63
    ma_period: int = 200
    adv_lookback: int = 20
    share_mode: ShareMode = ShareMode.WHOLE   # 기본 정수주(기존 동작 불변). FRACTIONAL=분수주 시뮬.
    lot_size: float = 0.001                   # 분수주 최소단위(브로커형).


# --- 데이터 품질 / 유동성 헬퍼 (순수) ---


def _data_quality_ok(df: pd.DataFrame, min_bars: int) -> bool:
    """OHLCV 데이터 품질: 길이/NaN/양수/high≥low. 예외나 결함이면 False."""
    try:
        if df is None or len(df) < min_bars:
            return False
        recent = df.tail(min(len(df), 60))
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                return False
            if pd.Series(recent[col], dtype="float64").isna().any():
                return False
        close = pd.Series(recent["close"], dtype="float64")
        high = pd.Series(recent["high"], dtype="float64")
        low = pd.Series(recent["low"], dtype="float64")
        vol = pd.Series(recent["volume"], dtype="float64")
        if (close <= 0).any() or (vol < 0).any() or (high < low).any():
            return False
        return True
    except Exception:  # noqa: BLE001 — 어떤 이상도 fail-closed.
        return False


def _avg_dollar_volume(df: pd.DataFrame, lookback: int) -> float:
    """평균 달러 거래량 = mean(close × volume, 최근 lookback). 산출 불가면 0.0."""
    try:
        close = pd.Series(df["close"], dtype="float64")
        vol = pd.Series(df["volume"], dtype="float64")
        dv = (close * vol).dropna()
        if len(dv) == 0:
            return 0.0
        return float(dv.tail(lookback).mean())
    except Exception:  # noqa: BLE001
        return 0.0


def _rs_confirmed(
    df: pd.DataFrame, candidate, benchmark_prices, lookback: int
) -> bool:
    """상대강도 확인. benchmark 있으면 직접 계산, 없으면 signal.rs, 둘 다 없으면 False(fail-closed)."""
    if benchmark_prices is not None:
        try:
            rs = relative_strength(
                pd.Series(df["close"], dtype="float64"),
                pd.Series(benchmark_prices, dtype="float64"),
                lookback=lookback,
            )
            return bool(rs) if rs is not None else False
        except Exception:  # noqa: BLE001
            return False
    rs = candidate.signal.relative_strength
    return bool(rs) if rs is not None else False


# --- 공개 빌더 ---


def build_candidate_context(
    candidate,
    df: pd.DataFrame,
    *,
    spy_prices,
    vix,
    params: EvidenceParams,
    benchmark_prices=None,
    event_provider: EventRiskProvider | None = None,
) -> CandidateContext:
    """후보 + 데이터에서 증거 8종 + 사이징을 파생해 CandidateContext를 만든다(fail-closed)."""
    symbol = candidate.symbol
    data_ok = _data_quality_ok(df, params.atr_period + 1)
    ipo_data_ok = df is not None and len(df) >= params.ma_period

    # 사이징(데이터 양호할 때만). 실패/불량 → 무효(qty 0, stop 0, per_trade inf) → hard-veto가 막음.
    stop_pct = 0.0
    ptr = _INF
    qty = 0
    reference_price = 0.0
    if data_ok:
        try:
            close = pd.Series(df["close"], dtype="float64")
            entry = float(close.iloc[-1])
            reference_price = entry
            atr = float(_atr(df, params.atr_period).iloc[-1])
            stop = stop_loss_price(entry, atr, params.atr_multiplier)
            stop_pct = stop_loss_pct(entry, stop)
            plan = position_size(
                params.account_equity, entry, stop,
                params.max_risk_pct, params.kelly_f, params.appetite_weight,
                share_mode=params.share_mode, lot_size=params.lot_size,
            )
            qty = plan.quantity
            ptr = per_trade_risk_pct(plan.risk_amount, params.account_equity)
        except Exception:  # noqa: BLE001 — 사이징 실패 = 데이터 이상 → 무효.
            data_ok = False
            stop_pct, ptr, qty, reference_price = 0.0, _INF, 0, 0.0

    # market regime (실패 → None → hard-veto가 막음).
    try:
        regime = classify_regime(spy_prices, vix, ma_period=params.ma_period)
    except Exception:  # noqa: BLE001
        regime = None

    filt = candidate.detail.get("filter")
    trend_confirmed = candidate.signal.overall == Signal.BULLISH
    volume_confirmed = bool(getattr(filt, "volume", False))
    rs_confirmed = _rs_confirmed(df, candidate, benchmark_prices, params.rs_lookback)
    technical_confirmation = trend_confirmed and volume_confirmed and rs_confirmed

    liquidity_ok = (
        data_ok and _avg_dollar_volume(df, params.adv_lookback) >= params.min_dollar_volume
    )
    # 날짜 인지 캘린더용 as_of = point-in-time df의 마지막 날짜(미래참조 없음).
    as_of = df.index[-1] if (df is not None and len(df) > 0) else None
    event_ok = bool(event_provider.is_clear(symbol, as_of)) if event_provider is not None else False

    return CandidateContext(
        stop_loss_pct=stop_pct,
        per_trade_risk_pct=ptr,
        regime=regime,
        quantity=qty,
        reference_price=reference_price,
        trend_confirmed=trend_confirmed,
        volume_confirmed=volume_confirmed,
        relative_strength_confirmed=rs_confirmed,
        liquidity_ok=liquidity_ok,
        tier_exposure_ok=True,  # dry-run 플랫 포트폴리오(포지션 없음). 실측은 RiskAgent 도메인(비범위).
        data_ok=data_ok,
        ipo_data_ok=ipo_data_ok,
        event_risk_checked=event_ok,
        technical_confirmation=technical_confirmation,
        manual_override=False,
    )


async def build_contexts(
    candidates,
    price_provider,
    *,
    spy_prices,
    vix,
    params: EvidenceParams,
    benchmark_prices=None,
    event_provider: EventRiskProvider | None = None,
) -> dict[str, CandidateContext]:
    """각 후보의 df를 동일 price_provider로 가져와 컨텍스트 dict를 만든다(phase1_flow 입력).

    scanner를 바꾸지 않고 같은 provider를 재사용한다.
    """
    contexts: dict[str, CandidateContext] = {}
    for cand in candidates:
        df = await price_provider.get_ohlcv(cand.symbol)
        contexts[cand.symbol] = build_candidate_context(
            cand, df, spy_prices=spy_prices, vix=vix, params=params,
            benchmark_prices=benchmark_prices, event_provider=event_provider,
        )
    return contexts
