// 워커 주문 영수증 패널 — dry-run 영수증 전용(실주문 없음).
// CRITICAL: 워커(Claude/Codex)가 reports/live_order_receipts.jsonl에 쓴 영수증을 backend가
// 읽어 표시한다. 어떤 영수증도 실주문이 아니다 — broker_order_id=null, real_order_placed=false,
// real_orders_placed=0. backend/프론트 모두 Robinhood write/order 도구를 호출하지 않는다.
import { Card } from "@/components/ui/Card";
import type { OrderReceipt } from "@/types";
import { formatUsd } from "@/lib/utils";

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function decisionToneClass(decision: string): string {
  if (decision === "WOULD_SUBMIT") return "font-semibold text-[#22c55e]";
  if (decision === "BLOCKED") return "font-semibold text-amber-400";
  if (decision === "ERROR") return "font-semibold text-[#ef4444]";
  return "font-semibold text-neutral-400";
}

export function OrderReceiptsPanel({
  receipts,
}: {
  /** 워커 영수증(읽기 전용). 최신이 마지막. 비어 있으면 안내 표시. */
  receipts: OrderReceipt[];
}) {
  const recent = receipts.slice(-5).reverse();

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">
          워커 주문 영수증
        </div>
        <span className="text-xs font-medium text-amber-400">
          dry-run receipt only — no real order submitted
        </span>
      </div>

      {recent.length === 0 ? (
        <div className="text-xs text-neutral-600">
          영수증 없음 (워커 대기 — dry-run only)
        </div>
      ) : (
        <div className="space-y-1">
          {recent.map((r) => (
            <div
              key={r.receipt_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-md bg-neutral-900 px-3 py-1.5 text-xs"
            >
              <span className="font-mono font-semibold text-white">
                {r.symbol}
              </span>
              <span className="text-neutral-500">{r.side}</span>
              <span className="tabular-nums text-neutral-400">
                {formatUsd(r.notional ?? 0)}
              </span>
              <span className={decisionToneClass(r.decision)}>{r.decision}</span>
              <span className="truncate text-neutral-500">{r.reason}</span>
              <span className="ml-auto tabular-nums text-neutral-600">
                {fmtTime(r.timestamp)}
              </span>
              <span className="w-full tabular-nums text-neutral-600">
                broker_order_id={String(r.broker_order_id)} · real_order_placed=
                {String(r.real_order_placed)} · real_orders_placed=
                {r.real_orders_placed}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
