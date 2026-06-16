import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

// 네트워크 의존성 제거: 백엔드 없이도 페이지가 렌더되는지(graceful) 검증한다.
const getWeeklyMock = vi.fn();
vi.mock("@/lib/api", () => ({
  getWeekly: () => getWeeklyMock(),
}));

// jsdom은 ResponsiveContainer 크기를 0으로 측정해 차트가 그려지지 않는다.
// 자식 차트에 고정 크기를 주입해 렌더만 검증한다(금지사항: 사이즈 0으로 깨지게 두지 마라).
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) =>
      React.cloneElement(children, { width: 800, height: 300 }),
  };
});

import WeeklyTradesPage from "@/app/weekly/page";

describe("③ 주간 거래기록 페이지", () => {
  it("차트 컨테이너를 렌더한다(백엔드 없이 graceful)", async () => {
    getWeeklyMock.mockResolvedValueOnce(null);
    render(await WeeklyTradesPage());
    expect(screen.getByTestId("weekly-chart")).toBeInTheDocument();
  });

  it("요일별 승률 히트맵을 7칸 렌더한다", async () => {
    getWeeklyMock.mockResolvedValueOnce(null);
    render(await WeeklyTradesPage());
    expect(screen.getAllByTestId("heatmap-cell")).toHaveLength(7);
  });

  it("크래시 없이 제목과 7개 요일 라벨을 표시한다", async () => {
    getWeeklyMock.mockResolvedValueOnce(null);
    render(await WeeklyTradesPage());
    expect(screen.getByText("주간 거래기록")).toBeInTheDocument();
    ["월", "화", "수", "목", "금", "토", "일"].forEach((d) =>
      expect(screen.getByText(d)).toBeInTheDocument(),
    );
  });
});
