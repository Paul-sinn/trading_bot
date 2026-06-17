import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// 네트워크 의존성 제거: 백엔드 없이도 페이지가 렌더되는지(graceful) 검증한다.
const getDirectionMock = vi.fn();
vi.mock("@/lib/api", () => ({
  getDirection: () => getDirectionMock(),
}));

import DirectionPage from "@/app/direction/page";

describe("④ 방향성 & AI 분석 페이지", () => {
  it("시황 요약 카드를 렌더한다(백엔드 없이 graceful)", async () => {
    getDirectionMock.mockResolvedValueOnce(null);
    render(await DirectionPage());
    expect(screen.getByTestId("market-summary")).toBeInTheDocument();
  });

  it("방향성 라벨(강세/중립/약세 중 하나)과 근거 카드를 표시한다", async () => {
    getDirectionMock.mockResolvedValueOnce(null);
    render(await DirectionPage());
    const label = screen.getByTestId("direction-label");
    expect(["강세", "중립", "약세"]).toContain(label.textContent);
    expect(screen.getByTestId("direction-rationale")).toBeInTheDocument();
  });

  it("크래시 없이 제목을 표시한다", async () => {
    getDirectionMock.mockResolvedValueOnce(null);
    render(await DirectionPage());
    expect(screen.getByText("방향성 & AI 분석")).toBeInTheDocument();
  });
});
