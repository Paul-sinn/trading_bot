"""사전 주문계획 진단 — 각 시뮬 진입에 대해 실행 전 명확한 주문계획을 만든다(순수 측정).

한정매수(limit_buy_shadow)로 진입을 표현하고, 청산 설정을 진입 전에 확정해 첨부한다. 상태/매매/체결/
veto를 바꾸지 않는다 — 어떤 계획도 실 트레이드에 적용하지 않는다(can_trade_live=False). 모멘텀 매매에
고정 전량 익절을 두지 않고, 하드 결정론 청산(stop/trailing/time-cut/max-holding)만 첨부한다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트 전용 — 동작 변경 없음(읽기만).

spec: specs/order_plan.md
"""

from __future__ import annotations

from dataclasses import dataclass

# 미체결 시 추격 금지 — 장 마감에 취소(AI/API 지연이 진입을 좌우하지 못하게).
ORDER_TIMEOUT_POLICY = "cancel_end_of_day"
DEFAULT_MAX_SLIPPAGE_PCT = 0.005     # 한정매수 슬리피지 상한 0.5%.


@dataclass(frozen=True)
class ExitProfile:
    """진입 전 확정되는 하드 결정론 청산 묶음. 고정 전량 익절 필드는 의도적으로 없다.

    partial_take_profit은 설명만(미강제) — None이 기본. time_cut_days는 옵션.
    """

    stop_loss_pct: float
    trailing_stop_pct: float
    max_holding_days: int
    time_cut_days: int | None = None
    partial_take_profit: str | None = None


# 현재 로버스트 기본(stop15/trail20/60d) + 레버리지 그림자 placeholder.
NORMAL_PROFILE = ExitProfile(stop_loss_pct=0.15, trailing_stop_pct=0.20, max_holding_days=60)
LEVERAGED_SHADOW_PROFILE = ExitProfile(
    stop_loss_pct=0.07, trailing_stop_pct=0.10, max_holding_days=10, time_cut_days=3,
)

_VALID_ROUTES = ("normal", "leveraged_shadow", "no_trade")


@dataclass(frozen=True)
class OrderPlan:
    """한 진입의 사전 주문계획(측정 보조 — 실행 아님)."""

    symbol: str
    entry_date: str | None
    reference_price: float | None
    max_entry_slippage_pct: float
    suggested_limit_price: float | None
    order_timeout_policy: str
    attached_exit_profile: ExitProfile | None
    route_type: str
    entry_order_type: str = "limit_buy_shadow"

    @property
    def can_trade_live(self) -> bool:
        return False

    @property
    def real_orders_placed(self) -> int:
        return 0


@dataclass(frozen=True)
class OrderPlanReport:
    """주문계획 묶음. can_trade_live False, real_orders_placed 0(항상)."""

    plans: tuple[OrderPlan, ...]

    @property
    def can_trade_live(self) -> bool:
        return False

    @property
    def real_orders_placed(self) -> int:
        return 0


def _profile_for_route(route: str, profile: ExitProfile | None) -> ExitProfile | None:
    """route별 기본 청산 프로파일. no_trade는 청산 없음. profile 지정 시 우선."""
    if route == "no_trade":
        return None
    if profile is not None:
        return profile
    return LEVERAGED_SHADOW_PROFILE if route == "leveraged_shadow" else NORMAL_PROFILE


def _safe_limit(reference_price, max_slippage_pct) -> float | None:
    """한정매수 상한가 = ref×(1+slippage). ref 결측/≤0이면 None(안전)."""
    if reference_price is None or reference_price <= 0:
        return None
    return float(reference_price) * (1.0 + max_slippage_pct)


def build_order_plan(
    symbol,
    entry_date,
    reference_price,
    *,
    route: str = "normal",
    max_slippage_pct: float = DEFAULT_MAX_SLIPPAGE_PCT,
    profile: ExitProfile | None = None,
) -> OrderPlan:
    """한 진입의 주문계획을 만든다(한정매수 + 진입 전 청산 첨부). 실행하지 않는다."""
    if route not in _VALID_ROUTES:
        route = "no_trade"
    exit_profile = _profile_for_route(route, profile)
    limit = None if route == "no_trade" else _safe_limit(reference_price, max_slippage_pct)
    return OrderPlan(
        symbol=symbol,
        entry_date=entry_date,
        reference_price=reference_price,
        max_entry_slippage_pct=max_slippage_pct,
        suggested_limit_price=limit,
        order_timeout_policy=ORDER_TIMEOUT_POLICY,
        attached_exit_profile=exit_profile,
        route_type=route,
    )


def compute_order_plan_diagnostics(
    trade_diag,
    *,
    profile: ExitProfile | None = None,
    max_slippage_pct: float = DEFAULT_MAX_SLIPPAGE_PCT,
) -> OrderPlanReport:
    """시뮬 진입 leg마다 normal 라우트 주문계획을 만든다(읽기 전용 — 입력 불변).

    같은 진입(symbol, entry_date, entry_price)이 FIFO로 여러 leg로 쪼개져도 주문 1건으로 dedupe.
    """
    plans: list[OrderPlan] = []
    seen: set[tuple] = set()
    for t in trade_diag.trades:
        key = (t.symbol, t.entry_date, t.entry_price)
        if key in seen:
            continue
        seen.add(key)
        plans.append(build_order_plan(
            t.symbol, t.entry_date, t.entry_price,
            route="normal", max_slippage_pct=max_slippage_pct, profile=profile,
        ))
    return OrderPlanReport(plans=tuple(plans))


def _fmt(value, fmt="{:.2f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _profile_str(prof: ExitProfile | None) -> str:
    if prof is None:
        return "(none)"
    s = f"stop {prof.stop_loss_pct:.0%} / trail {prof.trailing_stop_pct:.0%} / hold {prof.max_holding_days}d"
    if prof.time_cut_days is not None:
        s += f" / time_cut {prof.time_cut_days}d"
    s += f" / partial_TP {prof.partial_take_profit or '미강제(none)'}"
    return s


def format_order_plan(report: OrderPlanReport, *, max_rows: int = 60) -> str:
    """사람이 읽는 사전 주문계획 텍스트(측정 보조 — 실행 아님, 매매 미사용)."""
    lines: list[str] = []
    lines.append("=" * 84)
    lines.append("Pre-Trade Order Plan (측정 - 실주문 없음, 실행 아님)")
    lines.append("=" * 84)
    lines.append("entry=limit_buy_shadow, 청산은 진입 전 확정, 고정 전량 익절 없음(하드 결정론 청산만)")
    lines.append(f"  {'symbol':<8}{'entry_date':<12}{'ref_px':>10}{'limit_px':>10}{'slip':>7}{'route':>16}")
    for p in report.plans[:max_rows]:
        lines.append(
            f"  {p.symbol:<8}{(p.entry_date or '-'):<12}{_fmt(p.reference_price):>10}"
            f"{_fmt(p.suggested_limit_price):>10}{p.max_entry_slippage_pct:>6.1%}{p.route_type:>16}"
        )
        lines.append(f"      exit: {_profile_str(p.attached_exit_profile)}  timeout={p.order_timeout_policy}")
    if len(report.plans) > max_rows:
        lines.append(f"  ... (+{len(report.plans) - max_rows} more)")

    lines.append(
        f"profiles — normal: {_profile_str(NORMAL_PROFILE)}; "
        f"leveraged_shadow: {_profile_str(LEVERAGED_SHADOW_PROFILE)}"
    )
    lines.append("can_trade_live = false  (그림자 계획 — 실 라우팅/체결 없음)")
    lines.append(f"real_orders_placed : {report.real_orders_placed}")
    lines.append("=" * 84)
    return "\n".join(lines)
