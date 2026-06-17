import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import type { GoalPlan } from "@/types";

// 네트워크 의존성 제거: backend 호출은 mock 한다. 프론트는 backend REST만 부르므로
// (CLAUDE.md CRITICAL) lib/api 의 goal-plan 함수만 가로채면 충분하다.
const createGoalPlanMock = vi.fn();
const applyGoalPlanMock = vi.fn();
vi.mock("@/lib/api", () => ({
  createGoalPlan: (...args: unknown[]) => createGoalPlanMock(...args),
  applyGoalPlan: (...args: unknown[]) => applyGoalPlanMock(...args),
}));

import GoalsPage from "@/app/goals/page";

const samplePlan: GoalPlan = {
  settings: {
    appetite: 0.3,
    risk_limits: {
      max_risk_pct: 0.02,
      max_drawdown_pct: 0.1,
      max_position_pct: 0.2,
    },
    stop_loss_atr_multiplier: 2.0,
    feasibility: "ambitious",
    required_monthly_return: 0.05,
  },
  rationale: "테스트 근거 문구입니다.",
  summary: "요약 문구.",
  feasibility: "ambitious",
  required_monthly_return: 0.05,
};

describe("⑤ 목표 & 리스크 — AI 분석 패널", () => {
  beforeEach(() => {
    createGoalPlanMock.mockReset();
    applyGoalPlanMock.mockReset();
    applyGoalPlanMock.mockResolvedValue(null);
  });

  it("목표기간 입력과 모드 선택(안전/공격)을 렌더하고 '안전 한도 내 (추천)' 라벨이 있다", () => {
    render(<GoalsPage />);
    expect(screen.getByTestId("input-months")).toBeInTheDocument();
    expect(screen.getByText("안전 한도 내 (추천)")).toBeInTheDocument();
    expect(screen.getByText("목표 우선(공격적)")).toBeInTheDocument();
    expect(screen.getByTestId("analyze-btn")).toBeInTheDocument();
  });

  it("'AI 분석하기' 클릭 시 결과 패널에 세팅·실현가능성 배지·근거를 표시한다", async () => {
    createGoalPlanMock.mockResolvedValueOnce(samplePlan);
    render(<GoalsPage />);

    fireEvent.click(screen.getByTestId("analyze-btn"));

    const panel = await screen.findByTestId("ai-result-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("feasibility-badge")).toHaveTextContent("도전적");
    expect(screen.getByTestId("ai-rationale")).toHaveTextContent(
      "테스트 근거 문구입니다.",
    );
    // 역산 세팅(분수 → %) 표시 확인.
    expect(screen.getByTestId("setting-max-risk")).toHaveTextContent("2.0%");
    expect(screen.getByTestId("setting-appetite")).toBeInTheDocument();
  });

  it("결과가 있으면 '적용' 버튼이 있고 클릭 시 적용 동작한다", async () => {
    createGoalPlanMock.mockResolvedValueOnce(samplePlan);
    render(<GoalsPage />);

    fireEvent.click(screen.getByTestId("analyze-btn"));
    const applyBtn = await screen.findByTestId("apply-btn");
    expect(applyBtn).toBeInTheDocument();

    fireEvent.click(applyBtn);
    await waitFor(() => expect(applyGoalPlanMock).toHaveBeenCalledTimes(1));
  });

  it("백엔드 fetch 실패(null) 시 mock 계획으로 graceful fallback 한다(크래시 없음)", async () => {
    createGoalPlanMock.mockResolvedValueOnce(null);
    render(<GoalsPage />);

    fireEvent.click(screen.getByTestId("analyze-btn"));

    const panel = await screen.findByTestId("ai-result-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("feasibility-badge")).toBeInTheDocument();
    expect(screen.getByTestId("ai-rationale")).toBeInTheDocument();
  });

  it("기존 목표 진행 바·리스크 한도 설정이 여전히 존재한다(회귀 없음)", () => {
    render(<GoalsPage />);
    expect(screen.getByTestId("goal-progress")).toBeInTheDocument();
    expect(screen.getByTestId("input-drawdown")).toBeInTheDocument();
    expect(screen.getByTestId("input-max-position")).toBeInTheDocument();
  });
});
