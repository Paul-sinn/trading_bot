"""진입 한정가 민감도 매트릭스 — 여러 limit 버퍼의 다음-바 체결을 비교해 현실적 정책을 찾는다(순수 측정).

trade_diag(진입 reference/pnl) + price_data(OHLC)만 읽는다. 실제 시뮬 체결/포트폴리오/매매/veto를
바꾸지 않는다. 고정 버퍼 그리드 + next-open marketable proxy(worst-price-control)로 lookahead 없는
다음-바 체결을 추정할 뿐 — 어떤 정책도 실 트레이드에 적용하지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/what-if 전용 — 동작 변경 없음(읽기만).

spec: specs/entry_limit_sensitivity.md
"""

from __future__ import annotations

from dataclasses import dataclass

# 다음-바 위치/체결/PnL 헬퍼 단일 진실 재사용.
from agents.next_bar_fill_whatif import _classify, _next_bar, _pnl_map

# 고정 버퍼 그리드(최적화 아님).
BUFFER_GRID = (0.005, 0.01, 0.015, 0.02, 0.03)
_TIGHT_FILL = 0.85       # 체결률 < 0.85 → 빡빡.
_LOOSE_FILL = 0.98       # 체결률 ≥ 0.98 → 느슨(버퍼 정책 한정).
_RECOMMEND_FILL = 0.95   # 추천: 체결률 ≥ 0.95 최소 버퍼.


@dataclass(frozen=True)
class PolicyResult:
    """한 진입 한정가 정책의 다음-바 체결 결과(측정 보조)."""

    name: str
    buffer_pct: float | None     # None이면 marketable proxy.
    is_marketable: bool
    total: int
    filled: int
    missed: int
    unknown: int
    fill_rate: float | None
    profitable_missed_count: int
    missed_profitable_pnl: float
    avg_next_bar_gap: float | None
    avg_fill_premium: float | None
    est_filled_pnl: float        # 체결분 실 PnL 합 — what-if proxy(전체 포트폴리오 시뮬 아님).
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class EntryLimitSensitivityReport:
    """진입 한정가 민감도 묶음. real_orders_placed는 항상 0."""

    policies: tuple[PolicyResult, ...]
    best_by_fill_rate: PolicyResult | None
    best_by_est_pnl: PolicyResult | None
    recommended: PolicyResult | None
    warnings: tuple[str, ...]

    @property
    def real_orders_placed(self) -> int:
        return 0


def generate_buffers() -> tuple[float, ...]:
    """고정 버퍼 그리드(0.5%~3.0%)."""
    return BUFFER_GRID


def _entries(trade_diag):
    """(symbol, entry_date, reference_price) dedupe 목록."""
    out: list[tuple] = []
    seen: set[tuple] = set()
    for t in trade_diag.trades:
        key = (t.symbol, t.entry_date, t.entry_price)
        if key in seen:
            continue
        seen.add(key)
        out.append((t.symbol, t.entry_date, t.entry_price))
    return out


def _evaluate(entries, price_data, pnl_map, *, buffer, marketable) -> PolicyResult:
    """한 정책(버퍼 또는 marketable)을 다음-바로 평가한다."""
    filled = missed = unknown = 0
    premiums: list[float] = []
    gaps: list[float] = []
    est_pnl = 0.0
    profitable_missed = 0
    missed_profit_pnl = 0.0

    for symbol, entry_date, ref in entries:
        pnl = pnl_map.get((symbol, entry_date))
        nxt = _next_bar(price_data, symbol, entry_date)
        if nxt is None:
            unknown += 1
            continue
        _ndate, no, _nh, nl = nxt
        if ref and ref > 0:
            gaps.append(no / ref - 1.0)

        if marketable:
            status, fill_px = "filled", no       # 상한 무시 — 항상 next_open 체결.
        else:
            limit = ref * (1.0 + buffer)
            status, fill_px, _at = _classify(no, nl, limit)

        if status == "filled":
            filled += 1
            if ref and ref > 0 and fill_px is not None:
                premiums.append(fill_px / ref - 1.0)
            if pnl is not None:
                est_pnl += pnl
        else:  # missed
            missed += 1
            if pnl is not None and pnl > 0:
                profitable_missed += 1
                missed_profit_pnl += pnl

    known = filled + missed
    fill_rate = (filled / known) if known > 0 else None
    avg_gap = (sum(gaps) / len(gaps)) if gaps else None
    avg_premium = (sum(premiums) / len(premiums)) if premiums else None

    name = "next-open marketable" if marketable else f"buffer {buffer:.1%}"
    warnings: list[str] = []
    if marketable:
        warnings.append("worst-price-control: 항상 체결(갭 프리미엄 지불) — 체결 보장용 상한 모드")
    elif fill_rate is not None:
        if fill_rate < _TIGHT_FILL:
            warnings.append(f"빡빡: 체결률 {fill_rate:.0%} — 진입 누락 많음")
        elif fill_rate >= _LOOSE_FILL:
            warnings.append(f"느슨: 체결률 {fill_rate:.0%} — 거의 전량 체결")

    return PolicyResult(
        name=name, buffer_pct=(None if marketable else buffer), is_marketable=marketable,
        total=len(entries), filled=filled, missed=missed, unknown=unknown,
        fill_rate=fill_rate, profitable_missed_count=profitable_missed,
        missed_profitable_pnl=float(missed_profit_pnl), avg_next_bar_gap=avg_gap,
        avg_fill_premium=avg_premium, est_filled_pnl=float(est_pnl),
        warnings=tuple(warnings),
    )


def compute_entry_limit_sensitivity(trade_diag, price_data) -> EntryLimitSensitivityReport:
    """고정 버퍼 그리드 + marketable proxy의 다음-바 체결을 비교한다(읽기 전용 — 입력 불변)."""
    entries = _entries(trade_diag)
    pnl_map = _pnl_map(trade_diag)

    policies = [
        _evaluate(entries, price_data, pnl_map, buffer=b, marketable=False)
        for b in generate_buffers()
    ]
    policies.append(_evaluate(entries, price_data, pnl_map, buffer=None, marketable=True))

    rated = [p for p in policies if p.fill_rate is not None]
    best_fill = max(rated, key=lambda p: p.fill_rate) if rated else None
    best_pnl = max(policies, key=lambda p: p.est_filled_pnl) if policies else None

    # 추천: 체결률 ≥ 0.95인 최소 버퍼(없으면 None).
    recommended = next(
        (p for p in policies
         if not p.is_marketable and p.fill_rate is not None and p.fill_rate >= _RECOMMEND_FILL),
        None,
    )

    warnings: list[str] = []
    buffer_rated = [p for p in rated if not p.is_marketable]
    if buffer_rated and recommended is None:
        widest = max(buffer_rated, key=lambda p: p.buffer_pct or 0.0)
        warnings.append(
            f"가장 넓은 버퍼({widest.buffer_pct:.1%})도 체결률 {widest.fill_rate:.0%} < 95% — "
            f"marketable/더 넓은 상한 필요(갭 모멘텀)"
        )
    if recommended is not None:
        warnings.append(
            f"추천 진입 상한: {recommended.name} (체결률 {recommended.fill_rate:.0%}, "
            f"평균 프리미엄 {('%.2f%%' % (recommended.avg_fill_premium * 100)) if recommended.avg_fill_premium is not None else 'n/a'})"
        )

    return EntryLimitSensitivityReport(
        policies=tuple(policies), best_by_fill_rate=best_fill, best_by_est_pnl=best_pnl,
        recommended=recommended, warnings=tuple(warnings),
    )


def _fmt(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_entry_limit_sensitivity(report: EntryLimitSensitivityReport) -> str:
    """사람이 읽는 진입 한정가 민감도 텍스트(측정 보조 — 실행 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 92)
    lines.append("Entry Limit Sensitivity (측정 - 실주문 없음, 다음-바 lookahead 제거, 실 체결 불변)")
    lines.append("=" * 92)
    lines.append(
        f"  {'policy':<20}{'fill_rate':>10}{'filled':>7}{'missed':>7}{'unk':>5}"
        f"{'miss_profit':>12}{'avg_gap':>9}{'avg_prem':>9}{'est_pnl*':>10}"
    )
    for p in report.policies:
        lines.append(
            f"  {p.name:<20}{_fmt(p.fill_rate, '{:.0%}'):>10}{p.filled:>7}{p.missed:>7}{p.unknown:>5}"
            f"{p.missed_profitable_pnl:>12.2f}{_fmt(p.avg_next_bar_gap):>9}"
            f"{_fmt(p.avg_fill_premium):>9}{p.est_filled_pnl:>10.2f}"
        )
    lines.append("  (* est_pnl = 체결분 실 PnL 합 — what-if proxy, 전체 포트폴리오 시뮬 아님)")

    if report.best_by_fill_rate is not None:
        lines.append(f"best fill_rate : {report.best_by_fill_rate.name} ({_fmt(report.best_by_fill_rate.fill_rate, '{:.0%}')})")
    if report.best_by_est_pnl is not None:
        lines.append(f"best est_pnl   : {report.best_by_est_pnl.name} ({report.best_by_est_pnl.est_filled_pnl:.2f})")
    if report.recommended is not None:
        lines.append(f"recommended    : {report.recommended.name}")

    if report.warnings:
        lines.append("notes:")
        for w in report.warnings:
            lines.append(f"  ! {w}")

    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 92)
    return "\n".join(lines)
