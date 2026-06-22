// 포지션 / 청산 매니저 패널 — dry-run 전용(매도 주문 없음).
// CRITICAL: backend는 Robinhood MCP를 직접 호출하지 않고 워커 스냅샷만 읽는다. 청산 시그널은
// dry-run이며 어떤 매도 주문도 내지 않는다(broker_order_id=null, real_orders_placed=0).
import { Card } from "@/components/ui/Card";
import type { BrokerPosition, ExitDecision } from "@/types";
import { formatUsd, pnlColorClass } from "@/lib/utils";

function signalToneClass(signal: string): string {
  if (signal === "HOLD") return "font-semibold text-neutral-400";
  if (signal === "STOP_LOSS") return "font-semibold text-[#ef4444]";
  if (signal === "TRAILING_STOP" || signal === "TIME_STOP")
    return "font-semibold text-amber-400";
  if (signal === "MANUAL_CLOSE_DETECTED")
    return "font-semibold text-sky-400";
  return "font-semibold text-neutral-400";
}

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(2)}%`;
}

export function PositionExitManagerPanel({
  positions,
  exits,
}: {
  /** broker 스냅샷 기반 포지션(읽기 전용). */
  positions: BrokerPosition[];
  /** 최근 dry-run 청산 판단(읽기 전용). 최신이 마지막. */
  exits: ExitDecision[];
}) {
  const latest = exits.length > 0 ? exits[exits.length - 1] : null;
  const exitBySymbol = new Map(exits.map((e) => [e.symbol, e]));

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">
          포지션 / 청산 매니저
        </div>
        <span className="text-xs font-medium text-amber-400">
          Exit manager is dry-run only — no sell order submitted.
        </span>
      </div>

      {positions.length === 0 ? (
        <div className="text-xs text-neutral-600">
          포지션 없음 (broker 스냅샷 기준)
        </div>
      ) : (
        <div className="space-y-1">
          {positions.map((p) => {
            const e = exitBySymbol.get(p.symbol);
            return (
              <div
                key={p.symbol}
                className="flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded-md bg-neutral-900 px-3 py-1.5 text-xs"
              >
                <span className="font-mono font-semibold text-white">
                  {p.symbol}
                </span>
                <span className="tabular-nums text-neutral-500">
                  ×{p.quantity}
                </span>
                <span className="tabular-nums text-neutral-400">
                  avg {formatUsd(p.average_buy_price ?? 0)}
                </span>
                <span className="tabular-nums text-neutral-400">
                  cur {p.current_quote === null ? "—" : formatUsd(p.current_quote)}
                </span>
                <span
                  className={`tabular-nums ${pnlColorClass(p.unrealized_pnl_pct ?? 0)}`}
                >
                  {pct(p.unrealized_pnl_pct)}
                </span>
                <span className={`ml-auto ${signalToneClass(e?.exit_signal ?? "HOLD")}`}>
                  {e?.exit_signal ?? "—"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {latest && (
        <div className="border-t border-neutral-800 pt-2 text-xs text-neutral-500">
          최근 청산 판단:{" "}
          <span className={signalToneClass(latest.exit_signal)}>
            {latest.symbol} {latest.exit_signal}
          </span>{" "}
          — {latest.reason}
          <span className="ml-2 tabular-nums text-neutral-600">
            broker_order_id={String(latest.broker_order_id)} · real_orders_placed=
            {latest.real_orders_placed}
          </span>
        </div>
      )}
    </Card>
  );
}
