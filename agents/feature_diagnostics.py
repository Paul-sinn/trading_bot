"""피처 진단 — 트레이드 진입 시점의 FeatureSnapshot을 진단에 노출한다(순수 측정).

기존 산출물(trade_diagnostics의 트레이드 페어링)에서 (symbol, entry_date)를 얻고, price_data를
그 시점까지 슬라이스해(미래참조 금지) algorithms/features.compute_features로 피처를 계산한다. 상태/매매/
veto를 바꾸지 않는다 — 피처는 아직 매수/매도 판단에 쓰지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 전략 시그널 튜닝 없음.
LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/feature_diagnostics.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agents.trade_diagnostics import compute_trade_diagnostics
from algorithms.features import FeatureError, FeatureSnapshot, compute_features


@dataclass(frozen=True)
class FeatureRow:
    """한 진입(symbol×date)의 피처. snapshot=None이면 계산 불가(note에 사유)."""

    symbol: str
    context_date: str | None
    snapshot: FeatureSnapshot | None
    note: str | None = None


@dataclass(frozen=True)
class FeatureDiagnostics:
    """진입별 피처 묶음(측정 보조 — 판단 아님). real_orders_placed는 항상 0."""

    rows: tuple[FeatureRow, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def _slice_to(obj, as_of):
    """obj(Series/DataFrame)를 as_of까지 슬라이스(미래참조 금지). 불가/None이면 원본."""
    if as_of is None or not hasattr(obj, "loc"):
        return obj
    try:
        return obj.loc[: pd.Timestamp(as_of)]
    except (KeyError, TypeError, ValueError):
        return obj


def _feature_row(symbol, context_date, price_data, benchmark_prices) -> FeatureRow:
    """(symbol, date)의 피처 1행을 만든다. 데이터 없음/계산 불가는 snapshot=None + note(fail-safe)."""
    df = price_data.get(symbol) if price_data else None
    if df is None:
        return FeatureRow(symbol, context_date, None, note="가격 데이터 없음")

    sliced = _slice_to(df, context_date)
    bench = _slice_to(benchmark_prices, context_date) if benchmark_prices is not None else None
    try:
        snap = compute_features(sliced, symbol=symbol, benchmark=bench)
    except FeatureError as exc:
        return FeatureRow(symbol, context_date, None, note=f"피처 계산 불가: {exc}")
    return FeatureRow(symbol, context_date, snap, note=None)


def compute_feature_diagnostics(
    multiday,
    price_data,
    *,
    benchmark_prices=None,
    source_trades=None,
) -> FeatureDiagnostics:
    """트레이드 진입 시점의 피처 진단을 산출한다(읽기 전용).

    source_trades를 주면 그것을(.symbol/.entry_date) 쓰고, 없으면 compute_trade_diagnostics로
    트레이드 페어링을 재사용한다. (symbol, entry_date) 중복은 1행으로 dedupe.
    """
    if source_trades is not None:
        trades = source_trades
    else:
        trades = compute_trade_diagnostics(multiday).trades

    rows: list[FeatureRow] = []
    seen: set[tuple[str, str | None]] = set()
    for t in trades:
        key = (t.symbol, t.entry_date)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_feature_row(t.symbol, t.entry_date, price_data, benchmark_prices))

    return FeatureDiagnostics(rows=tuple(rows))


def _fmt(value, fmt="{:.2%}") -> str:
    """None은 n/a, bool은 그대로, 수치는 fmt."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "T" if value else "F"
    return fmt.format(value)


def format_feature_diagnostics(diag: FeatureDiagnostics, *, max_rows: int = 50) -> str:
    """사람이 읽는 피처 진단 텍스트(측정 보조 — 판단 아님)."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Feature Diagnostics (측정 - 실주문 없음, 매매 판단 미사용)")
    lines.append("=" * 70)

    if not diag.rows:
        lines.append("(트레이드 없음 — 표시할 피처 없음)")
        lines.append(f"real_orders_placed : {diag.real_orders_placed}")
        lines.append("=" * 70)
        return "\n".join(lines)

    lines.append(
        f"  {'symbol':<8}{'date':<12}{'mom':>8}{'r1m':>8}{'r3m':>8}{'r6m':>8}"
        f"{'rs':>8}{'vol×':>7}{'atr%':>7}{'fromHi':>8}  flags(20/50/20>50)"
    )
    for row in diag.rows[:max_rows]:
        s = row.snapshot
        if s is None:
            lines.append(f"  {row.symbol:<8}{(row.context_date or '-'):<12}  ({row.note})")
            continue
        flags = f"{_fmt(s.price_above_20ma)}/{_fmt(s.price_above_50ma)}/{_fmt(s.ma20_above_ma50)}"
        lines.append(
            f"  {s.symbol:<8}{(row.context_date or '-'):<12}"
            f"{_fmt(s.momentum_score):>8}{_fmt(s.return_1m):>8}{_fmt(s.return_3m):>8}"
            f"{_fmt(s.return_6m):>8}{_fmt(s.relative_strength):>8}"
            f"{_fmt(s.volume_ratio_20d, '{:.2f}'):>7}{_fmt(s.atr_pct):>7}"
            f"{_fmt(s.distance_from_high):>8}  {flags}"
        )
        if s.missing_fields:
            lines.append(f"      missing: {', '.join(s.missing_fields)}")

    if len(diag.rows) > max_rows:
        lines.append(f"  ... (+{len(diag.rows) - max_rows} more)")

    lines.append(f"real_orders_placed : {diag.real_orders_placed}")
    lines.append("=" * 70)
    return "\n".join(lines)
