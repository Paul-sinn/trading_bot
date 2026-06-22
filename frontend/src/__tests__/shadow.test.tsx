import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";

import type { ShadowBuy, ShadowReportView } from "@/types";

// 네트워크 제거: 프론트는 backend REST(/api/shadow)만 부르므로 lib/api 만 가로챈다(CLAUDE.md CRITICAL).
const getShadowReportMock = vi.fn();
const runDailyShadowMock = vi.fn();
vi.mock("@/lib/api", () => ({
  getShadowReport: (...args: unknown[]) => getShadowReportMock(...args),
  runDailyShadow: (...args: unknown[]) => runDailyShadowMock(...args),
}));

import ShadowReportPage from "@/app/shadow/page";

const sampleBuy: ShadowBuy = {
  symbol: "NVDA",
  decision_date: "2026-06-18",
  reason: "모멘텀 상위·추세 양호",
  shadow_score: 0.82,
  momentum_score: 0.31,
  volume_ratio_20d: 1.8,
  price_above_20ma: true,
  ma20_above_ma50: true,
  relative_strength: 0.12,
  distance_from_high: -0.03,
  riskgate_passed: true,
  riskgate_reasons: [],
  riskgate_result: "PASS",
  position_shares: 2.5,
  position_state: "held",
  is_reentry: true,
  previous_exit_reason: "trailing_stop",
  days_since_last_exit: 12,
  planned_entry_type: "next-bar-limit",
  entry_limit_buffer_pct: 0.03,
  planned_stop_loss: 0.15,
  planned_trailing_stop: 0.2,
  planned_max_holding: 60,
  real_orders_placed: 0,
};

function makeView(overrides: Partial<ShadowReportView> = {}): ShadowReportView {
  return {
    available: true,
    empty_message: null,
    run_command: "python -m experiments.daily_shadow_report",
    health_status: "PASS",
    health_findings: [],
    report_date: "2026-06-18",
    reference_date: "2026-06-18",
    selected_date: null,
    available_dates: ["2026-06-18", "2026-06-10"],
    n_buy: 1,
    n_reject: 0,
    n_skip: 19,
    riskgate_vetoes: 0,
    real_orders_placed: 0,
    buys: [sampleBuy],
    pending_counts: {},
    matured_counts: {},
    recent_outcomes: [],
    reentry_total: 0,
    reentry_count: 0,
    concentration_warnings: [],
    daily_markdown: null,
    ...overrides,
  };
}

describe("섀도 리포트 — BUY 사전검토 / 주문 계획 (report-only)", () => {
  beforeEach(() => {
    getShadowReportMock.mockReset();
    runDailyShadowMock.mockReset();
  });

  it("BUY 카드에 필수 필드(심볼·지표·RiskGate·재진입·주문 계획)를 렌더한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);

    const card = await screen.findByTestId("buy-card-NVDA");
    expect(within(card).getByText("NVDA")).toBeInTheDocument();
    expect(within(card).getByText("2026-06-18")).toBeInTheDocument();
    expect(within(card).getByText(/RiskGate: PASS/)).toBeInTheDocument();
    expect(within(card).getByText(/포지션: held/)).toBeInTheDocument();
    expect(within(card).getByText("모멘텀 상위·추세 양호")).toBeInTheDocument();
    // 시그널 지표.
    expect(within(card).getByText("shadow")).toBeInTheDocument();
    expect(within(card).getByText("momentum")).toBeInTheDocument();
    expect(within(card).getByText("volume×")).toBeInTheDocument();
    // 재진입 컨텍스트.
    expect(within(card).getByText(/직전 청산 사유: trailing_stop/)).toBeInTheDocument();
  });

  it("주문 계획 섹션은 'simulated plan only'를 명시하고 real_orders_placed=0을 표시한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);

    const plan = await screen.findByTestId("order-plan-NVDA");
    expect(within(plan).getByText(/This is a simulated plan only/)).toBeInTheDocument();
    expect(within(plan).getByText(/real_orders_placed = 0/)).toBeInTheDocument();
    expect(within(plan).getByText(/next-bar-limit/)).toBeInTheDocument();
  });

  it("주문/브로커/Robinhood 동작 버튼이 없다(report-only)", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    await screen.findByTestId("buy-card-NVDA");

    const buttons = screen.getAllByRole("button").map((b) => b.textContent ?? "");
    for (const label of buttons) {
      expect(label.toLowerCase()).not.toMatch(/order|buy now|주문|매수|robinhood|broker|체결/);
    }
  });

  it("BUY 0이면 'No BUY signals today. Strategy is waiting.' + SKIP/REJECT 요약을 표시한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(
      makeView({ n_buy: 0, buys: [], n_skip: 19, n_reject: 2, riskgate_vetoes: 1 }),
    );
    render(<ShadowReportPage />);

    const empty = await screen.findByTestId("buy-empty-state");
    expect(within(empty).getByText("No BUY signals today. Strategy is waiting.")).toBeInTheDocument();
    expect(within(empty).getByText(/SKIP 19/)).toBeInTheDocument();
    expect(within(empty).getByText(/REJECT 2/)).toBeInTheDocument();
    expect(screen.queryByTestId("buy-card-NVDA")).not.toBeInTheDocument();
  });

  it("real_orders_placed는 항상 0으로 표시된다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    await screen.findByTestId("buy-card-NVDA");
    expect(screen.getByText("real_orders_placed = 0")).toBeInTheDocument();
  });

  it("날짜 선택 드롭다운(available_dates)을 렌더한다(과거 BUY 예시 리뷰)", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    const select = await screen.findByTestId("date-select");
    expect(within(select).getByText("2026-06-18")).toBeInTheDocument();
    expect(within(select).getByText("2026-06-10")).toBeInTheDocument();
  });

  it("백엔드 실패(null) 시 빈 상태로 graceful 처리한다(크래시 없음)", async () => {
    getShadowReportMock.mockResolvedValueOnce(null);
    render(<ShadowReportPage />);
    await waitFor(() =>
      expect(screen.getByText("섀도 리포트가 아직 없습니다.")).toBeInTheDocument(),
    );
  });
});
