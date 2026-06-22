// 실주문 실행 준비 패널 — scaffold 전용(실주문 없음).
// CRITICAL: 실주문 실행은 기본 비활성이며, 현재 단계엔 실 Robinhood 주문 경로가 결선돼 있지 않다.
// 이 패널은 읽기 전용 상태(활성여부/arm/cap/오늘 실주문 수/최근 차단사유)만 표시한다.
import { Card } from "@/components/ui/Card";
import type { ExecutionStatus } from "@/types";
import { formatUsd } from "@/lib/utils";

const ARM_LABEL: Record<string, string> = {
  missing: "없음",
  disarmed: "해제",
  expired: "만료",
  armed: "무장",
};

export function ExecutionReadinessPanel({
  status,
}: {
  /** 실행 준비 상태(읽기 전용). null이면 비활성으로 간주. */
  status: ExecutionStatus | null;
}) {
  const enabled = status?.real_execution_enabled ?? false;

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">실주문 실행 준비</div>
        <span
          className={`text-xs font-semibold ${enabled ? "text-amber-400" : "text-neutral-500"}`}
        >
          {enabled ? "ENABLED" : "DISABLED"}
        </span>
      </div>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
        Real order execution is disabled. Scaffold only — no Robinhood order
        submitted.
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <Item
          label="실행 활성"
          value={enabled ? "true" : "false"}
          tone={enabled ? "warn" : "muted"}
        />
        <Item
          label="수동 arm"
          value={ARM_LABEL[status?.arm_status ?? "missing"] ?? "없음"}
          tone={status?.arm_status === "armed" ? "warn" : "muted"}
        />
        <Item
          label="실주문 상한"
          value={formatUsd(status?.max_notional_per_real_order_usd ?? 0)}
        />
        <Item
          label="오늘 실주문"
          value={`${status?.real_orders_today ?? 0} / ${status?.max_real_orders_per_day ?? 0}`}
        />
        <Item
          label="real_orders_placed"
          value={String(status?.real_orders_placed ?? 0)}
          mono
        />
        <Item
          label="최근 판정"
          value={status?.latest_decision ?? "—"}
          tone={status?.latest_decision === "REAL_READY_DRY_RUN" ? "warn" : "muted"}
        />
        <Item
          label="최근 차단 사유"
          value={status?.latest_block_reason ?? "—"}
          mono
        />
      </div>
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
  tone?: "default" | "warn" | "muted";
  mono?: boolean;
}) {
  const toneClass = {
    default: "text-white",
    warn: "text-amber-400",
    muted: "text-neutral-400",
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
