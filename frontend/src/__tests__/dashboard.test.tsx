import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { mockPortfolio } from "@/lib/mock";

// 네트워크/WS 의존성 제거: 백엔드 없이도 페이지가 렌더되는지(graceful) 검증한다.
vi.mock("@/lib/api", () => ({
  getPortfolio: vi.fn().mockResolvedValue(mockPortfolio),
}));
vi.mock("@/lib/ws", () => ({
  // WS는 구독하지 않고 즉시 해제 함수만 돌려준다 (jsdom에 WebSocket 없음).
  subscribeTicker: vi.fn(() => () => {}),
}));

import DashboardPage from "@/app/page";

describe("① 대시보드 페이지", () => {
  it("크래시 없이 렌더되고 ① 핵심 요소(총자산·오늘 손익·승률)를 표시한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("총자산")).toBeInTheDocument();
    expect(screen.getByText("오늘 손익")).toBeInTheDocument();
    expect(screen.getByText("승률")).toBeInTheDocument();
  });

  it("실시간 리스크% 게이지를 표시한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("리스크")).toBeInTheDocument();
  });

  it("봇 ON/OFF 토글(switch)을 렌더한다", async () => {
    render(await DashboardPage());
    expect(screen.getByRole("switch")).toBeInTheDocument();
  });

  it("실시간 가격 티커를 mock으로라도 표시한다(백엔드 없이 graceful)", async () => {
    render(await DashboardPage());
    // mockTicker의 기본 워치리스트 심볼 중 하나가 보여야 한다.
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });
});
