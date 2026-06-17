import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import ProfilePage from "@/app/profile/page";

describe("⑥ 투자성향 설정 페이지", () => {
  it("성향 슬라이더를 양 끝 라벨(보수적/공격적)과 함께 렌더한다", () => {
    render(<ProfilePage />);
    expect(screen.getByText("투자성향 설정")).toBeInTheDocument();
    expect(screen.getByRole("slider")).toBeInTheDocument();
    expect(screen.getByText("보수적")).toBeInTheDocument();
    expect(screen.getByText("공격적")).toBeInTheDocument();
  });

  it("슬라이더 변경 시 사이징 미리보기 값이 갱신된다", () => {
    render(<ProfilePage />);
    const before = (screen.getByTestId("preview-weight") as HTMLElement)
      .textContent;
    fireEvent.change(screen.getByRole("slider"), { target: { value: "100" } });
    const after = (screen.getByTestId("preview-weight") as HTMLElement)
      .textContent;
    expect(after).not.toBe(before);
    expect(after).toContain("1.00");
  });

  it("섹터 화이트/블랙리스트·매매 시간대·알림 토글이 존재한다", () => {
    render(<ProfilePage />);
    expect(screen.getByTestId("sector-whitelist")).toBeInTheDocument();
    expect(screen.getByTestId("sector-blacklist")).toBeInTheDocument();
    expect(screen.getByTestId("time-start")).toBeInTheDocument();
    expect(screen.getByTestId("time-end")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-slack")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-sms")).toBeInTheDocument();
  });
});
