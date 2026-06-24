// 장중 오케스트레이터 패널 — 자동으로 스캔→라우터→승인요청까지 수행함을 보여준다(읽기 전용).
// CRITICAL: 오케스트레이터는 Discord 승인 요청만 만든다. 절대 주문을 제출하지 않는다.
// 이 패널은 상태만 표시하며 실행/주문을 트리거하지 않는다.
import { Card } from "@/components/ui/Card";
import type { OrchestratorStatus } from "@/types";

export function MarketOrchestratorPanel({
  status,
}: {
  /** 오케스트레이터 상태(읽기 전용). null이면 비활성으로 간주. */
  status: OrchestratorStatus | null;
}) {
  const enabled = status?.enabled ?? false;
  const running = status?.running ?? false;
  const marketOpen = status?.market_open ?? false;

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">Market Orchestrator</div>
        <span className={`text-xs font-semibold ${running ? "text-emerald-400" : "text-neutral-500"}`}>
          {running ? "RUNNING" : "STOPPED"}
        </span>
      </div>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
        Orchestrator only creates Discord approval requests. It never submits
        orders.
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <Item label="활성" value={enabled ? "enabled" : "disabled"} tone={enabled ? "text-amber-400" : "text-neutral-400"} />
        <Item label="실행" value={running ? "running" : "stopped"} tone={running ? "text-emerald-400" : "text-neutral-400"} />
        <Item label="시장" value={marketOpen ? "open" : "closed"} tone={marketOpen ? "text-emerald-400" : "text-neutral-500"} />
        <Item label="마지막 실행" value={status?.last_run_at ?? "—"} mono />
        <Item label="최근 라우터 결정" value={status?.last_router_decision ?? "—"} />
        <Item label="대기 승인 id" value={status?.pending_approval_id ?? "—"} mono />
        <Item
          label="오늘 승인요청"
          value={status ? `${status.approvals_today}/${status.max_approvals_per_day}` : "—"}
        />
        <Item label="오늘 실주문" value={String(status?.real_orders_today ?? 0)} />
        <Item label="차단 사유" value={status?.last_reason ?? "—"} mono />
        <Item
          label="Discord 봇 준비"
          value={status ? (status.discord_worker_ready ? "ready" : "미설정") : "—"}
          tone={status?.discord_worker_ready ? "text-emerald-400" : "text-neutral-500"}
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
