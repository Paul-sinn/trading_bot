import { cn } from "@/lib/utils";

interface GaugeProps {
  /** 0 ~ 100 */
  value: number;
  label?: string;
  className?: string;
}

// UI_GUIDE 게이지: 트랙 bg-neutral-800, 채움은 값에 따라 녹색→주황→적색, 퍼센트 병기.
function fillColor(v: number): string {
  if (v >= 80) return "#ef4444";
  if (v >= 50) return "#f59e0b";
  return "#22c55e";
}

export function Gauge({ value, label, className }: GaugeProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("space-y-1", className)}>
      {label && (
        <div className="flex justify-between text-sm">
          <span className="text-neutral-400">{label}</span>
          <span className="tabular-nums text-white">{clamped.toFixed(1)}%</span>
        </div>
      )}
      <div className="h-2 w-full rounded-full bg-neutral-800">
        <div
          className="h-2 rounded-full transition-all"
          style={{ width: `${clamped}%`, backgroundColor: fillColor(clamped) }}
        />
      </div>
    </div>
  );
}
