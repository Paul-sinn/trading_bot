import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { mockTrades } from "@/lib/mock";

// 네트워크 의존성 제거: 백엔드 없이도 페이지가 렌더되는지(graceful) 검증한다.
const getTradesMock = vi.fn();
vi.mock("@/lib/api", () => ({
  getTrades: () => getTradesMock(),
}));

import DailyTradesPage from "@/app/daily/page";

describe("② 일간 거래기록 페이지", () => {
  it("테이블 헤더 5개 컬럼(티커·진입가·청산가·실현손익·AI 메모)을 렌더한다", async () => {
    getTradesMock.mockResolvedValueOnce(mockTrades);
    render(await DailyTradesPage());
    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(5);
    expect(screen.getByText("티커")).toBeInTheDocument();
    expect(screen.getByText("진입가")).toBeInTheDocument();
    expect(screen.getByText("청산가")).toBeInTheDocument();
    expect(screen.getByText("실현손익")).toBeInTheDocument();
    expect(screen.getByText("AI 메모")).toBeInTheDocument();
  });

  it("mock 거래 행을 표시한다(백엔드 없이 graceful)", async () => {
    getTradesMock.mockResolvedValueOnce(null);
    render(await DailyTradesPage());
    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("TSLA")).toBeInTheDocument();
  });

  it("실현손익 컬럼만 시맨틱 색상을 적용한다(이익 녹색/손실 적색)", async () => {
    getTradesMock.mockResolvedValueOnce(mockTrades);
    render(await DailyTradesPage());
    const profit = screen.getByText("+$50.00");
    const loss = screen.getByText("−$50.00");
    expect(profit.className).toContain("text-[#22c55e]");
    expect(loss.className).toContain("text-[#ef4444]");
  });

  it("거래 0건이면 빈 상태('오늘 체결 없음')를 표시한다", async () => {
    getTradesMock.mockResolvedValueOnce([]);
    render(await DailyTradesPage());
    expect(screen.getByText("오늘 체결 없음")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
