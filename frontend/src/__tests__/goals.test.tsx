import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import GoalsPage from "@/app/goals/page";

describe("⑤ 목표 & 리스크 페이지", () => {
  it("크래시 없이 제목과 목표 진행 바를 렌더한다", () => {
    render(<GoalsPage />);
    expect(screen.getByText("목표 & 리스크")).toBeInTheDocument();
    expect(screen.getByTestId("goal-progress")).toBeInTheDocument();
  });

  it("드로우다운 한도 / 최대 포지션 크기 입력 필드를 표시한다", () => {
    render(<GoalsPage />);
    expect(screen.getByTestId("input-drawdown")).toBeInTheDocument();
    expect(screen.getByTestId("input-max-position")).toBeInTheDocument();
  });

  it("입력 변경이 로컬 상태에 반영된다", () => {
    render(<GoalsPage />);
    const drawdown = screen.getByTestId("input-drawdown") as HTMLInputElement;
    fireEvent.change(drawdown, { target: { value: "25" } });
    expect(drawdown.value).toBe("25");
  });
});
