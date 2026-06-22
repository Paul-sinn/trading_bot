// 브로커 스냅샷 패널 — read-only 워커 브리지 v0.
// CRITICAL: backend는 Robinhood MCP를 직접 호출하지 않고, Claude/Codex MCP 워커가 적재한
// reports/broker_snapshots.jsonl 최신본만 읽어 표시한다. 주문 경로 없음(real_orders_placed=0).
// 스냅샷이 없거나 오래되면(staleness) 경고를 표시한다.
import { Card } from "@/components/ui/Card";
import type { BrokerSnapshot } from "@/types";
import { formatUsd } from "@/lib/utils";

// 이보다 오래된 스냅샷은 stale로 간주(backend broker_snapshot_max_age_seconds와 동일 의도).
const STALE_THRESHOLD_MS = 3600 * 1000;

function fmtSync(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function ageMs(iso: string): number | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : Date.now() - d.getTime();
}

export function BrokerSnapshotPanel({
  snapshot,
}: {
  /** 최신 브로커 스냅샷(읽기 전용). 없으면 null → 경고 표시. */
  snapshot: BrokerSnapshot | null;
}) {
  const age = snapshot ? ageMs(snapshot.timestamp) : null;
  const stale = snapshot !== null && (age === null || age > STALE_THRESHOLD_MS);

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">
          브로커 스냅샷
        </div>
        <span className="text-xs font-medium text-amber-400">
          read-only broker snapshot — no orders
        </span>
      </div>

      {snapshot === null ? (
        <div className="rounded-md border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-400">
          브로커 스냅샷 없음 — MCP 워커가 아직 적재하지 않음 (잔고/포지션 미표시)
        </div>
      ) : (
        <>
          {stale && (
            <div className="rounded-md border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-400">
              스냅샷이 오래됨(stale) — 워커 재실행 필요
            </div>
          )}
          {snapshot.errors.length > 0 && (
            <div className="rounded-md border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-[#ef4444]">
              스냅샷 오류 {snapshot.errors.length}건: {snapshot.errors.join(" · ")}
            </div>
          )}

          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
            <Item label="계정" value={snapshot.account_last4} mono />
            <Item label="마지막 동기화" value={fmtSync(snapshot.timestamp)} mono />
            <Item
              label="상태"
              value={stale ? "stale" : "최신"}
              tone={stale ? "warn" : "good"}
            />
            <Item label="총자산" value={formatUsd(snapshot.total_value ?? 0)} />
            <Item label="현금" value={formatUsd(snapshot.cash ?? 0)} />
            <Item
              label="매수가능"
              value={formatUsd(snapshot.buying_power ?? 0)}
            />
            <Item label="포지션 수" value={String(snapshot.positions.length)} />
            <Item
              label="미체결 주문"
              value={String(snapshot.open_orders.length)}
            />
            <Item label="호가 수" value={String(snapshot.quotes.length)} />
          </div>

          <div className="flex items-center justify-between border-t border-neutral-800 pt-2 text-xs text-neutral-600">
            <span>provider: {snapshot.provider}</span>
            <span className="tabular-nums">
              real_orders_placed = {snapshot.real_orders_placed}
            </span>
          </div>
        </>
      )}
    </Card>
  );
}

function Item({
  label,
  value,
  tone = "default",
  mono = false,
}: {
  label: string;
  value: string;
  tone?: "default" | "good" | "warn";
  mono?: boolean;
}) {
  const toneClass = {
    default: "text-white",
    good: "text-[#22c55e]",
    warn: "text-amber-400",
  }[tone];
  return (
    <div className="space-y-0.5">
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div
        className={`${toneClass} ${mono ? "font-mono text-xs" : "text-sm font-semibold"} truncate tabular-nums`}
      >
        {value}
      </div>
    </div>
  );
}
