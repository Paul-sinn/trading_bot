// 레짐 패널 — 최신 스캔의 레짐/소스/VIX/위험축소를 보여준다(읽기 전용).
// CRITICAL: VIX 폴백은 레짐 필터에만 쓰이며 종목/주문 가격엔 사용되지 않는다. 주문 없음.
import { Card } from "@/components/ui/Card";
import type { RegimeStatus } from "@/types";

const BULLISH = new Set(["NORMAL_BULL", "NERVOUS_BULL", "spy_bull_vix_unknown"]);

export function RegimePanel({
  status,
}: {
  /** 레짐 상태(읽기 전용). null이면 미상으로 표시. */
  status: RegimeStatus | null;
}) {
  const regime = status?.regime ?? null;
  const tone = regime
    ? BULLISH.has(regime)
      ? "text-emerald-400"
      : "text-red-400"
    : "text-neutral-500";

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-neutral-400">레짐 필터</div>
        <span className={`text-xs font-semibold ${tone}`}>{regime ?? "—"}</span>
      </div>

      {status?.warning ? (
        <div className="rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-xs text-amber-400">
          {status.warning}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <Item label="레짐" value={regime ?? "—"} tone={tone} />
        <Item label="소스" value={status?.regime_source ?? "—"} />
        <Item label="VIX" value={status?.vix_value != null ? String(status.vix_value) : "—"} />
        <Item
          label="위험 축소"
          value={status?.risk_reduced ? "true" : "false"}
          tone={status?.risk_reduced ? "text-amber-400" : undefined}
        />
        <Item label="기준 시각" value={status?.as_of ?? "—"} mono />
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
