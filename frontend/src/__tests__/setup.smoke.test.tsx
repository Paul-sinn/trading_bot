import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

describe("UI 프리미티브 스모크", () => {
  it("Button(Primary)이 크래시 없이 렌더되고 UI_GUIDE 클래스를 쓴다", () => {
    render(<Button>매수</Button>);
    const btn = screen.getByRole("button", { name: "매수" });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toContain("bg-white");
    expect(btn.className).toContain("rounded-lg");
  });

  it("Danger 버튼은 적색 시맨틱 색상(#ef4444)을 쓴다", () => {
    render(<Button variant="danger">정지</Button>);
    const btn = screen.getByRole("button", { name: "정지" });
    expect(btn.className).toContain("bg-[#ef4444]");
  });

  it("Card가 카드 표면 색(#141414)과 테두리로 렌더된다", () => {
    const { container } = render(<Card>내용</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card).toBeInTheDocument();
    expect(card.className).toContain("bg-[#141414]");
    expect(card.className).toContain("border-neutral-800");
  });
});
