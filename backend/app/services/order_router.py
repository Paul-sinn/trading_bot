"""자동 주문 라우터 v1 — 전략 생성 BUY 후보 1개를 자동 선택해 $100 이하 실주문 프리뷰를 만들고
Discord 승인 요청을 보낸다(감독 거래).

CRITICAL 안전 불변식:
- **주문을 제출하지 않는다.** 라우터는 후보 선택 + 프리뷰 + 승인 요청 생성까지만 한다. 실제 제출은
  Discord 승인 게이트 뒤의 별도 단계에서만(이 task에선 실주문 0).
- Robinhood write/order/cancel/review MCP 도구를 import/호출하지 않는다.
- 모든 안전 게이트(전략 intent만·테스트성 차단·일일 캡·신선 스냅샷·정규장·스프레드·호가 신선도)를 적용한다.
- `real_orders_placed=0` 항상. 승인은 리스크 게이트를 우회하지 않는다.

흐름: accepted_dry_run BUY OrderIntent들 → 필터 → 결정론적 랭킹 → 1개 선택 → 주문유형 정책($100 캡:
지정가/분수 시장가) → 승인 요청 생성(Discord 전송). 자격 후보 없으면 ROUTER_BLOCKED.

spec: specs/real_order_v1_checklist.md §11
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import floor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.core.config import Settings
from backend.app.services.approval_store import (
    ApprovalRequestRefused,
    count_requests_today,
    create_approval_request,
    get_request_for_intent,
)
from backend.app.services.broker_snapshot import BrokerSnapshot, is_stale, latest_snapshot
from backend.app.services.candidate_pipeline import ORDER_INTENTS_LOG
from backend.app.services.execution_gate import OrderIntent
from backend.app.services.real_order_executor import (
    daily_real_order_count,
    executed_keys,
    is_market_open,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"
ROUTER_DECISIONS_LOG = "order_router_decisions.jsonl"

RouterDecision = Literal["ROUTER_SELECTED", "ROUTER_BLOCKED"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


# --- 호가 ---
class RouterQuote(BaseModel):
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    as_of: str | None = None

    @property
    def reference_price(self) -> float | None:
        """주문 가격 기준 — ask 우선, 없으면 last."""
        return self.ask if self.ask is not None else self.last

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid

    def age_seconds(self, *, snapshot: BrokerSnapshot, now: datetime | None = None) -> float | None:
        """호가 나이(초). as_of 있으면 그것으로, 없으면 스냅샷 timestamp로 폴백."""
        now = now or _now()
        ts = self.as_of or snapshot.timestamp
        try:
            t = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return None
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (now - t).total_seconds()


def _quote_for(
    snapshot: BrokerSnapshot, symbol: str, *, live_quotes: dict[str, RouterQuote] | None = None
) -> RouterQuote | None:
    # 우선순위: 라이브 호가(Alpaca 등) → 브로커 스냅샷 호가. 라이브는 ref price/spread/freshness용.
    if live_quotes and symbol in live_quotes:
        return live_quotes[symbol]
    for q in snapshot.quotes:
        if isinstance(q, dict) and q.get("symbol") == symbol:
            return RouterQuote(symbol=symbol, bid=q.get("bid"), ask=q.get("ask"),
                               last=q.get("price") if q.get("last") is None else q.get("last"),
                               as_of=q.get("as_of"))
    return None


def _norm_ts(ts: str | None) -> str | None:
    """Alpaca RFC3339(나노초/Z 포함)를 fromisoformat 호환(마이크로초 + +00:00)으로 정규화."""
    if not ts:
        return ts
    import re

    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", ts)
    if not m:
        return ts
    base, frac, tz = m.group(1), (m.group(2) or ""), (m.group(3) or "+00:00")
    if frac:
        frac = frac[:7]  # 소수점 + 최대 6자리(마이크로초)
    return base + frac + ("+00:00" if tz == "Z" else tz)


def _alpaca_router_quotes(symbols: set[str], settings: Settings) -> dict[str, RouterQuote]:
    """MARKET_DATA_PROVIDER=alpaca일 때 후보 심볼의 라이브 호가를 가져온다. 오류/미설정은 fail-safe({})."""
    if (settings.market_data_provider or "").strip().lower() != "alpaca" or not symbols:
        return {}
    try:
        from backend.app.services.market_data import get_market_data_provider

        prov = get_market_data_provider(settings)
        if getattr(prov, "name", "") != "alpaca":
            return {}
        mq = prov.get_batch_latest_quotes(sorted(symbols))  # type: ignore[attr-defined]
        return {
            sym: RouterQuote(symbol=sym, bid=q.bid, ask=q.ask, last=q.last, as_of=_norm_ts(q.quote_timestamp))
            for sym, q in mq.items()
        }
    except Exception:  # noqa: BLE001 - fail-safe: 호가 없음 → 후보가 막힘(approval 생성 안 됨)
        return {}


def _has_open_buy(snapshot: BrokerSnapshot, symbol: str) -> bool:
    for o in snapshot.open_orders:
        if isinstance(o, dict) and o.get("symbol") == symbol and str(o.get("side", "")).lower() == "buy":
            return True
    return False


# --- 결과 모델 ---
class SelectedPreview(BaseModel):
    symbol: str
    side: str = "BUY"
    order_type: str  # limit | market
    notional: float
    quantity: float | None = None
    dollar_amount: float | None = None
    limit_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    spread_pct: float | None = None
    strategy_id: str
    source_intent_id: str
    confidence: float | None = None
    score: float


class OrderRouterResult(BaseModel):
    timestamp: str = Field(default_factory=_now_iso)
    decision: RouterDecision
    reason: str = ""
    block_reasons: list[str] = Field(default_factory=list)
    selected: SelectedPreview | None = None
    approval_id: str | None = None
    candidates_considered: int = 0
    real_orders_placed: int = 0  # 불변식: 라우터는 주문을 내지 않는다.

    def model_post_init(self, _ctx) -> None:
        object.__setattr__(self, "real_orders_placed", 0)


# --- intent 로딩 ---
def _load_intents(reports_dir: Path | None, limit: int = 200) -> list[OrderIntent]:
    path = (reports_dir or DEFAULT_REPORTS_DIR) / ORDER_INTENTS_LOG
    if not path.exists():
        return []
    out: list[OrderIntent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(OrderIntent.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def _eligible(intent: OrderIntent, *, settings: Settings, snapshot: BrokerSnapshot,
              executed: set[str], reports_dir: Path | None) -> list[str]:
    """단일 intent의 자격 위반 사유(빈 리스트면 자격 있음)."""
    reasons: list[str] = []
    # 전략/라이브스캔 생성 + 테스트성 차단
    if intent.strategy_id != settings.live_strategy_id and not settings.test_only_intent_real_order_allowed:
        reasons.append("test-only/non-strategy intent")
    if intent.side != "BUY":
        reasons.append("BUY only")
    if getattr(intent, "asset_type", "equity") != "equity":
        reasons.append("equity only")
    if intent.mock_llm_decision != "approve":
        reasons.append("LLM/mock review not approved")
    if intent.execution_gate_status != "accepted_dry_run":
        reasons.append("not accepted_dry_run")
    if intent.scan_event_key in executed:
        reasons.append("이미 실행/접수됨 (executed)")
    if get_request_for_intent(intent.scan_event_key, reports_dir=reports_dir) is not None:
        reasons.append("이미 승인 요청 존재 (중복)")
    if _has_open_buy(snapshot, intent.symbol):
        reasons.append("중복 미체결 매수 주문 존재")
    return reasons


def _score(intent: OrderIntent, quote: RouterQuote, *, settings: Settings, snapshot: BrokerSnapshot,
           now: datetime) -> float:
    """결정론적 점수: 높은 신뢰도 선호 + 좁은 스프레드 선호 + 신선한 호가 선호."""
    conf = intent.mock_llm_confidence or 0.0
    spread = quote.spread_pct or 0.0
    age = quote.age_seconds(snapshot=snapshot, now=now) or 0.0
    spread_penalty = (spread / settings.order_router_max_spread_pct) * 0.1 if settings.order_router_max_spread_pct else 0.0
    age_penalty = (age / settings.order_router_quote_max_age_seconds) * 0.1 if settings.order_router_quote_max_age_seconds else 0.0
    return round(conf - spread_penalty - age_penalty, 6)


def _quote_block_reasons(quote: RouterQuote | None, *, settings: Settings, snapshot: BrokerSnapshot,
                         now: datetime) -> list[str]:
    if quote is None or quote.reference_price is None:
        return ["호가 없음 (missing quote)"]
    reasons: list[str] = []
    age = quote.age_seconds(snapshot=snapshot, now=now)
    if age is None or age > settings.order_router_quote_max_age_seconds:
        reasons.append("호가 stale")
    spread = quote.spread_pct
    if spread is None:
        reasons.append("스프레드 계산 불가 (bid/ask 없음)")
    elif spread > settings.order_router_max_spread_pct:
        reasons.append("스프레드 과다")
    return reasons


def _build_preview(intent: OrderIntent, quote: RouterQuote, *, settings: Settings,
                   score: float) -> tuple[SelectedPreview | None, list[str]]:
    """주문유형 정책 적용($100 캡). 성공 시 (SelectedPreview, []), 실패 시 (None, [사유])."""
    cap = settings.order_router_max_notional_usd
    ref = quote.reference_price
    assert ref is not None  # 호출 전 _quote_block_reasons로 보장

    def _preview(order_type: str, *, notional: float, quantity: float | None = None,
                 dollar_amount: float | None = None, limit_price: float | None = None) -> SelectedPreview:
        return SelectedPreview(
            symbol=intent.symbol, order_type=order_type, notional=notional, quantity=quantity,
            dollar_amount=dollar_amount, limit_price=limit_price, bid=quote.bid, ask=quote.ask,
            last=quote.last, spread_pct=quote.spread_pct, strategy_id=intent.strategy_id,
            source_intent_id=intent.scan_event_key, confidence=intent.mock_llm_confidence, score=score,
        )

    if ref <= cap:
        base = quote.ask if quote.ask is not None else ref
        limit_price = round(min(base * (1.0 + settings.order_router_limit_buffer_pct), cap), 2)
        if limit_price <= 0:
            return None, ["지정가 계산 실패"]
        qty = max(1, floor(cap / limit_price))
        notional = round(qty * limit_price, 2)
        if notional > cap:  # floor로 보장되지만 방어적으로 1주 줄임
            qty = max(1, qty - 1)
            notional = round(qty * limit_price, 2)
        if notional > cap:
            return None, ["1주도 $100 캡 초과"]
        return _preview("limit", quantity=float(qty), limit_price=limit_price, notional=notional), []

    # ref > cap → 분수 시장가 매수 정책
    if not settings.order_router_allow_fractional_market_buy:
        return None, ["고가주: 분수 시장가 매수 비활성"]
    if (intent.mock_llm_confidence or 0.0) < settings.order_router_min_confidence_for_fractional:
        return None, ["고가주: 분수 매수 최소 신뢰도 미달"]
    return _preview("market", dollar_amount=cap, notional=cap), []


def _global_blocks(*, settings: Settings, snapshot: BrokerSnapshot | None, now: datetime,
                   market_open: bool | None, reports_dir: Path | None) -> list[str]:
    reasons: list[str] = []
    if snapshot is None:
        reasons.append("broker snapshot 없음")
    elif settings.require_fresh_broker_snapshot_for_real_order and is_stale(
        snapshot, max_age_seconds=settings.broker_snapshot_max_age_seconds, now=now
    ):
        reasons.append("broker snapshot stale")
    mo = is_market_open(now) if market_open is None else market_open
    if settings.require_market_hours_for_real_order and not mo:
        reasons.append("장시간 아님")
    if daily_real_order_count(reports_dir=reports_dir, now=now) >= settings.max_real_orders_per_day:
        reasons.append("MAX_REAL_ORDERS_PER_DAY 초과")
    if count_requests_today(reports_dir=reports_dir, now=now) >= settings.order_router_daily_max_approval_requests:
        reasons.append("ORDER_ROUTER_DAILY_MAX_APPROVAL_REQUESTS 초과")
    return reasons


def select_and_route(
    *,
    settings: Settings | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    market_open: bool | None = None,
    intents: list[OrderIntent] | None = None,
    snapshot: BrokerSnapshot | None = None,
    live_quotes: dict[str, RouterQuote] | None = None,
    send: bool = True,
    post=None,
) -> OrderRouterResult:
    """후보를 선택해 프리뷰 + 승인 요청을 만든다(주문 제출 없음). 결과를 jsonl에 기록한다.

    호가는 라이브(Alpaca, MARKET_DATA_PROVIDER=alpaca)를 우선 쓰고 없으면 브로커 스냅샷으로 폴백한다.
    """
    settings = settings or Settings()
    now = now or _now()
    snapshot = snapshot if snapshot is not None else latest_snapshot(reports_dir=reports_dir)
    intents = intents if intents is not None else _load_intents(reports_dir)
    if live_quotes is None:  # provider=alpaca면 후보 심볼의 라이브 호가를 가져온다(fail-safe).
        live_quotes = _alpaca_router_quotes({i.symbol for i in intents}, settings)

    gb = _global_blocks(settings=settings, snapshot=snapshot, now=now, market_open=market_open, reports_dir=reports_dir)
    if gb:
        return _persist(OrderRouterResult(decision="ROUTER_BLOCKED", reason=gb[0], block_reasons=gb,
                                          candidates_considered=len(intents)), reports_dir=reports_dir)
    assert snapshot is not None
    executed = executed_keys(reports_dir=reports_dir)

    # 자격 통과 + 호가 통과 후보만 점수화. 중복 source_intent_id는 가장 최근 것만.
    seen: set[str] = set()
    scored: list[tuple[float, OrderIntent, RouterQuote]] = []
    considered = 0
    for intent in reversed(intents):  # 최신 우선
        if intent.scan_event_key in seen:
            continue
        seen.add(intent.scan_event_key)
        considered += 1
        if _eligible(intent, settings=settings, snapshot=snapshot, executed=executed, reports_dir=reports_dir):
            continue
        quote = _quote_for(snapshot, intent.symbol, live_quotes=live_quotes)
        if _quote_block_reasons(quote, settings=settings, snapshot=snapshot, now=now):
            continue
        assert quote is not None
        scored.append((_score(intent, quote, settings=settings, snapshot=snapshot, now=now), intent, quote))

    if not scored:
        return _persist(OrderRouterResult(decision="ROUTER_BLOCKED", reason="자격 후보 없음",
                                          block_reasons=["자격/호가 통과 후보 없음"],
                                          candidates_considered=considered), reports_dir=reports_dir)

    # 결정론적 랭킹: 점수 내림차순, 동점은 symbol 알파벳 오름차순.
    scored.sort(key=lambda t: (-t[0], t[1].symbol))
    score, intent, quote = scored[0]

    preview, pv_reasons = _build_preview(intent, quote, settings=settings, score=score)
    if preview is None:
        return _persist(OrderRouterResult(decision="ROUTER_BLOCKED", reason=pv_reasons[0],
                                          block_reasons=pv_reasons, candidates_considered=considered),
                        reports_dir=reports_dir)

    # 승인 요청용 합성 intent(라우터 프리뷰 반영) — 원본 출처/전략 유지.
    routed_intent = intent.model_copy(update={
        "planned_order_type": preview.order_type,
        "planned_limit_price": preview.limit_price,
        "planned_quantity": preview.quantity,
        "planned_notional_usd": preview.notional,
    })
    try:
        req = create_approval_request(
            routed_intent, type="BUY", settings=settings, snapshot=snapshot, now=now,
            reports_dir=reports_dir, send=send, post=post,
            bid=quote.bid, ask=quote.ask, last=quote.last, spread_pct=quote.spread_pct,
        )
    except ApprovalRequestRefused as exc:
        return _persist(OrderRouterResult(decision="ROUTER_BLOCKED", reason=str(exc),
                                          block_reasons=[str(exc)], candidates_considered=considered),
                        reports_dir=reports_dir)

    return _persist(OrderRouterResult(
        decision="ROUTER_SELECTED", reason="후보 선택 + 승인 요청 생성(주문 없음)",
        selected=preview, approval_id=req.approval_id, candidates_considered=considered,
    ), reports_dir=reports_dir)


# --- 영속화 + 읽기 전용 상태 ---
def _persist(result: OrderRouterResult, *, reports_dir: Path | None) -> OrderRouterResult:
    path = (reports_dir or DEFAULT_REPORTS_DIR) / ROUTER_DECISIONS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.model_dump(), ensure_ascii=False) + "\n")
    return result


def load_router_decisions(*, limit: int = 50, reports_dir: Path | None = None) -> list[OrderRouterResult]:
    limit = max(1, min(int(limit), 500))
    path = (reports_dir or DEFAULT_REPORTS_DIR) / ROUTER_DECISIONS_LOG
    if not path.exists():
        return []
    out: list[OrderRouterResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(OrderRouterResult.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]


def latest_router_decision(*, reports_dir: Path | None = None) -> OrderRouterResult | None:
    rs = load_router_decisions(limit=1, reports_dir=reports_dir)
    return rs[-1] if rs else None


class OrderRouterStatus(BaseModel):
    """라우터 설정 + 일일 카운트 요약(읽기 전용 — 선택/승인 실행 안 함)."""

    max_notional_usd: float
    allow_fractional_market_buy: bool
    max_spread_pct: float
    quote_max_age_seconds: int
    daily_max_approval_requests: int
    approval_requests_today: int
    real_orders_today: int
    market_open: bool
    latest_decision: str | None = None
    latest_reason: str | None = None
    latest_approval_id: str | None = None
    real_orders_placed: int = 0


def router_status(*, settings: Settings | None = None, reports_dir: Path | None = None,
                  now: datetime | None = None) -> OrderRouterStatus:
    settings = settings or Settings()
    now = now or _now()
    latest = latest_router_decision(reports_dir=reports_dir)
    return OrderRouterStatus(
        max_notional_usd=settings.order_router_max_notional_usd,
        allow_fractional_market_buy=settings.order_router_allow_fractional_market_buy,
        max_spread_pct=settings.order_router_max_spread_pct,
        quote_max_age_seconds=settings.order_router_quote_max_age_seconds,
        daily_max_approval_requests=settings.order_router_daily_max_approval_requests,
        approval_requests_today=count_requests_today(reports_dir=reports_dir, now=now),
        real_orders_today=daily_real_order_count(reports_dir=reports_dir, now=now),
        market_open=is_market_open(now),
        latest_decision=latest.decision if latest else None,
        latest_reason=latest.reason if latest else None,
        latest_approval_id=latest.approval_id if latest else None,
        real_orders_placed=0,
    )
