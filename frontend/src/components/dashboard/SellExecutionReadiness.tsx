// 수동 매도 실행 준비 패널 — scaffold 전용(실 매도 없음).
// CRITICAL: 매도 실행은 기본 비활성이며 현재 단계엔 실 Robinhood 매도 경로가 결선돼 있지 않다.
// 읽기 전용 상태(활성/arm/매도가능 포지션/최근 판정·차단사유)만 표시한다.
import { Card } from "@/components/ui/Card";
import type { SellExecutionStatus } from "@/types";

const ARM_LABEL: Record<string, string> = {
  missing: "없음",
  disarmed: "해제",
  expired: "만료",
  armed: "무장",
};

export function SellExecutionReadinessPanel({
  status,
}: {
  /** 매도 실행 준비 상태(읽기 전용). null이면 비활성으로 간주. */
  status: SellExecutionStatus | null;
}) {
  const enabled = status?.allow_real_sell_orders ?? false;
  const positions = status?.sellable_positions ?? [];

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">수동 매도 실행 준비</div>
        <span
          className={`text-xs font-semibold ${enabled ? "text-amber-400" : "text-neutral-500"}`}
        >
          {enabled ? "ENABLED" : "DISABLED"}
        </span>
      </div>

      <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
        Manual sell execution is scaffold only — no Robinhood sell order
        submitted.
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <Item label="매도 실행 활성" value={enabled ? "true" : "false"} tone={enabled ? "warn" : "muted"} />
        <Item
          label="수동 매도 arm"
          value={ARM_LABEL[status?.sell_arm_status ?? "missing"] ?? "없음"}
          tone={status?.sell_arm_status === "armed" ? "warn" : "muted"}
        />
        <Item
          label="프로덕션 최근 판정"
          value={status?.latest_decision ?? "—"}
          tone={status?.latest_decision === "SELL_READY_DRY_RUN" ? "warn" : "muted"}
        />
        <Item label="real_sell_orders_placed" value={String(status?.real_sell_orders_placed ?? 0)} mono />
        <Item label="최근 차단 사유" value={status?.latest_block_reason ?? "—"} mono />
      </div>

      <div className="border-t border-neutral-800 pt-2">
        <div className="text-xs font-medium text-neutral-500">매도 가능 포지션</div>
        {positions.length === 0 ? (
          <div className="text-xs text-neutral-600">없음</div>
        ) : (
          <div className="mt-1 space-y-0.5">
            {positions.map((p) => (
              <div key={p.symbol} className="flex gap-x-3 text-xs">
                <span className="font-mono font-semibold text-white">{p.symbol}</span>
                <span className="tabular-nums text-neutral-400">
                  보유 {p.quantity ?? "—"} · 매도가능 {p.shares_available_for_sells ?? "—"}
                </span>
              </div>
            ))}
          </div>
        )}
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
  const toneClass = { default: "text-white", warn: "text-amber-400", muted: "text-neutral-400" }[tone];
  return (
    <div className="space-y-0.5">
      <div className="text-xs font-medium text-neutral-500">{label}</div>
      <div className={`${toneClass} ${mono ? "font-mono text-xs" : "text-sm font-semibold"} truncate tabular-nums`}>
        {value}
      </div>
    </div>
  );
}
