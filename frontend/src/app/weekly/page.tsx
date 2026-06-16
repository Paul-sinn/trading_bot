// ③ 주간 거래기록 — 7일 캔들차트 + 누적 손익 라인 오버레이, 요일별 승률 히트맵.
// Server Component. 데이터: getWeekly()(REST) 시도 → 실패 시 mockWeekly 폴백.
// 차트(Recharts)만 Client Component로 격리. 색상은 UI_GUIDE 시맨틱 팔레트만 사용.
import { Card } from "@/components/ui/Card";
import { WeeklyChart } from "@/components/weekly/WeeklyChart";
import { getWeekly } from "@/lib/api";
import { mockWeekly } from "@/lib/mock";

// 승률(0~1) → 상승색(#22c55e) 농도. 높을수록 진하게(opacity 0.12~0.92).
function heatStyle(winRate: number): React.CSSProperties {
  const alpha = (0.12 + winRate * 0.8).toFixed(2);
  return { backgroundColor: `rgba(34, 197, 94, ${alpha})` };
}

export default async function WeeklyTradesPage() {
  const weekly = (await getWeekly()) ?? mockWeekly;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <h1 className="text-2xl font-semibold text-white">주간 거래기록</h1>

      <Card>
        <h2 className="mb-4 text-sm font-medium text-neutral-400">
          7일 가격 + 누적 손익
        </h2>
        <WeeklyChart bars={weekly.bars} />
      </Card>

      <Card>
        <h2 className="mb-4 text-sm font-medium text-neutral-400">
          요일별 승률
        </h2>
        <div className="grid grid-cols-7 gap-2">
          {weekly.win_rates.map((d) => (
            <div
              key={d.day}
              data-testid="heatmap-cell"
              className="flex flex-col items-center gap-1 rounded-md border border-neutral-800 py-4"
              style={heatStyle(d.win_rate)}
            >
              <span className="text-xs text-neutral-400">{d.day}</span>
              <span className="text-sm font-medium tabular-nums text-white">
                {Math.round(d.win_rate * 100)}%
              </span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
