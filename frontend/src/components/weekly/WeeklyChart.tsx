"use client";

import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { WeeklyBar } from "@/types";

// ③ 7일 캔들차트 + 누적 손익 라인 오버레이. Recharts는 Client Component로 격리.
// 색상은 UI_GUIDE 시맨틱 팔레트만: 상승 #22c55e / 하락 #ef4444 / 라인·축·그리드 중립.
const UP = "#22c55e";
const DOWN = "#ef4444";
const NEUTRAL = "#a3a3a3"; // 누적 손익 라인 (중립)
const AXIS = "#525252"; // 축/그리드 (중립)

export function WeeklyChart({ bars }: { bars: WeeklyBar[] }) {
  // 캔들 표현: 심지(low~high) + 몸통(open~close 범위). 상승/하락은 Cell 색으로 구분.
  const data = bars.map((b) => ({
    date: b.date,
    wick: [b.low, b.high] as [number, number],
    body: [Math.min(b.open, b.close), Math.max(b.open, b.close)] as [
      number,
      number,
    ],
    up: b.close >= b.open,
    cumulative_pnl: b.cumulative_pnl,
  }));

  return (
    <div data-testid="weekly-chart" className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
        >
          <CartesianGrid stroke="#262626" vertical={false} />
          <XAxis
            dataKey="date"
            stroke={AXIS}
            tick={{ fontSize: 11, fill: "#737373" }}
            tickLine={false}
          />
          <YAxis
            yAxisId="price"
            stroke={AXIS}
            tick={{ fontSize: 11, fill: "#737373" }}
            tickLine={false}
            domain={["auto", "auto"]}
            width={40}
          />
          <YAxis
            yAxisId="pnl"
            orientation="right"
            stroke={AXIS}
            tick={{ fontSize: 11, fill: "#737373" }}
            tickLine={false}
            width={40}
          />
          <Tooltip
            contentStyle={{
              background: "#141414",
              border: "1px solid #262626",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#a3a3a3" }}
            cursor={{ fill: "#ffffff0d" }}
          />
          {/* 심지 (얇은 Bar) */}
          <Bar yAxisId="price" dataKey="wick" barSize={2} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={`wick-${i}`} fill={d.up ? UP : DOWN} />
            ))}
          </Bar>
          {/* 몸통 (open~close 범위 Bar) */}
          <Bar yAxisId="price" dataKey="body" barSize={12} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={`body-${i}`} fill={d.up ? UP : DOWN} />
            ))}
          </Bar>
          {/* 누적 손익 라인 오버레이 (우측 축, 중립색) */}
          <Line
            yAxisId="pnl"
            type="monotone"
            dataKey="cumulative_pnl"
            stroke={NEUTRAL}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
