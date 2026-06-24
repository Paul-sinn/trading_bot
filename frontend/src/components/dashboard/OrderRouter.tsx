// 자동 주문 라우터 패널 — 봇이 고른 후보 + 프리뷰를 보여준다(읽기 전용).
// CRITICAL: 봇이 거래를 선택하지만, 실주문 전에는 여전히 Discord 승인이 필요하다. 승인은 리스크 게이트를
// 우회하지 않는다. 이 패널은 jsonl만 읽으며 어떤 주문도 내지 않는다.
import { Card } from "@/components/ui/Card";
import type { OrderRouterResult, OrderRouterStatus } from "@/types";

export function OrderRouterPanel({
  status,
  latest,
}: {
  /** 라우터 설정 + 일일 카운트(읽기 전용). */
  status: OrderRouterStatus | null;
  /** 가장 최근 라우터 결정(없으면 null). */
  latest: OrderRouterResult | null;
}) {
  const sel = latest?.selected ?? null;
  const decided = latest?.decision ?? null;

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">자동 주문 라우터</div>
        <span
          className={`text-xs font-semibold ${
            decided === "ROUTER_SELECTED"
              ? "text-emerald-400"
              : decided === "ROUTER_BLOCKED"
                ? "text-amber-400"
                : "text-neutral-500"
          }`}
        >
          {decided ?? "—"}
        </span>
      </div>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
        Bot selects the trade. Discord approval is still required before any real
        order.
      </div>

      <div className="border-t border-neutral-800 pt-2">
        <div className="text-xs font-medium text-neutral-500">선택된 후보</div>
        {sel === null ? (
          <div className="text-xs text-neutral-600">
            {latest?.reason ? `없음 — ${latest.reason}` : "없음"}
          </div>
        ) : (
          <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
            <Item label="종목" value={sel.symbol} />
            <Item label="주문유형" value={sel.order_type} />
            <Item
              label="노셔널"
              value={`$${sel.notional}`}
              tone="text-emerald-400"
            />
            <Item
              label={sel.order_type === "limit" ? "지정가/수량" : "달러"}
              value={
                sel.order_type === "limit"
                  ? `$${sel.limit_price} × ${sel.quantity}`
                  : `$${sel.dollar_amount}`
              }
            />
            <Item
              label="스프레드%"
              value={
                sel.spread_pct != null
                  ? `${(sel.spread_pct * 100).toFixed(3)}%`
                  : "—"
              }
            />
            <Item
              label="bid/ask/last"
              value={`${sel.bid ?? "—"}/${sel.ask ?? "—"}/${sel.last ?? "—"}`}
              mono
            />
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-neutral-800 pt-2 text-sm sm:grid-cols-3">
        <Item label="라우터 결정" value={decided ?? "—"} />
        <Item
          label="승인 상태"
          value={latest?.approval_id ? "승인 요청됨(PENDING)" : "—"}
          tone={latest?.approval_id ? "text-amber-400" : undefined}
        />
        <Item
          label="오늘 승인요청"
          value={
            status
              ? `${status.approval_requests_today}/${status.daily_max_approval_requests}`
              : "—"
          }
        />
        <Item label="최대 노셔널" value={status ? `$${status.max_notional_usd}` : "—"} />
        <Item
          label="분수 시장가"
          value={status ? (status.allow_fractional_market_buy ? "허용" : "비활성") : "—"}
        />
        <Item label="real_orders_placed" value={String(status?.real_orders_placed ?? 0)} mono />
      </div>
    </Card>
  );
}

function Item({
  label,
  value,
  tone,
  mono = false,
}: {
  label: string;
  value: string;
  tone?: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div
        className={`${tone ?? "text-white"} ${mono ? "font-mono text-xs" : "text-sm font-semibold"} truncate tabular-nums`}
      >
        {value}
      </div>
    </div>
  );
}
