import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";

import type {
  ShadowBuy,
  ShadowDecisionDetail,
  ShadowOutcomeDetail,
  ShadowReportView,
} from "@/types";

// 네트워크 제거: 프론트는 backend REST(/api/shadow)만 부르므로 lib/api 만 가로챈다(CLAUDE.md CRITICAL).
const getShadowReportMock = vi.fn();
const runDailyShadowMock = vi.fn();
vi.mock("@/lib/api", () => ({
  getShadowReport: (...args: unknown[]) => getShadowReportMock(...args),
  runDailyShadow: (...args: unknown[]) => runDailyShadowMock(...args),
}));

import ShadowReportPage from "@/app/shadow/page";

const matureOutcome: ShadowOutcomeDetail = {
  scorable: true,
  mature: true,
  return_1d: -0.014,
  return_5d: 0.037,
  return_10d: 0.076,
  return_20d: 0.066,
  return_60d: 0.057,
  mfe: 0.112,
  mae: -0.017,
  stop_hit: false,
  trail_hit: false,
  time_close: true,
};

const sampleBuy: ShadowBuy = {
  symbol: "NVDA",
  decision_date: "2025-08-01",
  reason: "모멘텀 상위·추세 양호",
  record_mode: "historical",
  outcome: matureOutcome,
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
  position_shares: 0,
  position_state: "flat",
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

const sampleDecisions: ShadowDecisionDetail[] = [
  {
    symbol: "NVDA",
    decision: "BUY",
    date: "2025-08-01",
    riskgate_result: "PASS",
    is_reentry: true,
    position_state: "flat",
    record_mode: "historical",
    outcome: matureOutcome,
  },
  {
    symbol: "MU",
    decision: "REJECT",
    date: "2025-08-01",
    riskgate_result: "VETO",
    is_reentry: false,
    position_state: "flat",
    record_mode: "historical",
    outcome: null,
  },
];

function makeView(overrides: Partial<ShadowReportView> = {}): ShadowReportView {
  return {
    available: true,
    empty_message: null,
    run_command: "python -m experiments.daily_shadow_report",
    health_status: "PASS",
    health_findings: [],
    report_date: "2025-08-01",
    reference_date: "2026-06-18",
    selected_date: "2025-08-01",
    available_dates: ["2026-06-18", "2025-08-01"],
    n_buy: 1,
    n_reject: 1,
    n_skip: 19,
    riskgate_vetoes: 1,
    real_orders_placed: 0,
    latest_ledger_date: "2026-06-18",
    has_mature_outcomes: true,
    buys: [sampleBuy],
    decisions_detail: sampleDecisions,
    missed_winners: [],
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

describe("섀도 리포트 — 리뷰 폴리시 (report-only)", () => {
  beforeEach(() => {
    getShadowReportMock.mockReset();
    runDailyShadowMock.mockReset();
  });

  it("BUY 카드에 결과 연결 필드(returns/MFE/MAE/stop/trailing/time)를 렌더한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);

    const card = await screen.findByTestId("buy-card-NVDA");
    expect(within(card).getByText("forward 결과 (report-only)")).toBeInTheDocument();
    expect(within(card).getByText("5.7%")).toBeInTheDocument(); // 60d
    expect(within(card).getByText(/MFE 11.2%/)).toBeInTheDocument();
    expect(within(card).getByText(/MAE -1.7%/)).toBeInTheDocument();
    expect(within(card).getByText(/stop ✗/)).toBeInTheDocument();
    expect(within(card).getByText(/time_stop ✓/)).toBeInTheDocument();
  });

  it("미성숙 결과는 'pending'으로 표시한다", async () => {
    const pendingOutcome: ShadowOutcomeDetail = {
      ...matureOutcome,
      mature: false,
      return_5d: null,
      return_10d: null,
      return_20d: null,
      return_60d: null,
    };
    getShadowReportMock.mockResolvedValueOnce(
      makeView({
        has_mature_outcomes: false,
        buys: [{ ...sampleBuy, outcome: pendingOutcome }],
        decisions_detail: [{ ...sampleDecisions[0], outcome: pendingOutcome }],
      }),
    );
    render(<ShadowReportPage />);

    const card = await screen.findByTestId("buy-card-NVDA");
    expect(within(card).getAllByText("pending").length).toBeGreaterThan(0);
    expect(screen.getByTestId("outcomes-pending")).toBeInTheDocument();
  });

  it("planned quantity가 0/미상이면 'not sized / report-only'로 표시한다(0.0000 금지)", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    const qty = await screen.findByTestId("planned-qty-NVDA");
    expect(qty).toHaveTextContent("not sized / report-only");
    expect(qty).not.toHaveTextContent("0.0000");
  });

  it("historical 레코드는 backfill 배지 + 'not a live trade' 카피를 표시한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    const card = await screen.findByTestId("buy-card-NVDA");
    expect(within(card).getByText("historical/backfill")).toBeInTheDocument();
    expect(
      within(card).getByText("Historical simulation record — not a live trade."),
    ).toBeInTheDocument();
  });

  it("포지션 상태는 report-only/시뮬로 라벨한다(실보유 암시 금지)", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    const pos = await screen.findByTestId("position-state-NVDA");
    expect(pos).toHaveTextContent(/report-only/);
    expect(pos).toHaveTextContent(/flat/);
  });

  it("필터 버튼은 빈 데이터에서도 크래시하지 않는다", async () => {
    getShadowReportMock.mockResolvedValueOnce(
      makeView({ decisions_detail: sampleDecisions }),
    );
    render(<ShadowReportPage />);
    await screen.findByTestId("filter-all");

    // BUY 필터 → NVDA만, SKIP 필터 → 없음(빈 상태 메시지, 크래시 없음).
    fireEvent.click(screen.getByTestId("filter-SKIP"));
    expect(screen.getByTestId("filter-empty")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("filter-best60"));
    expect(screen.queryByTestId("filter-empty")).not.toBeInTheDocument();
  });

  it("missed-winner 섹션은 데이터 없으면 안전하게 미표시", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView({ missed_winners: [] }));
    render(<ShadowReportPage />);
    await screen.findByTestId("buy-card-NVDA");
    expect(screen.queryByTestId("missed-winners")).not.toBeInTheDocument();
  });

  it("missed-winner 섹션은 데이터 있으면 historical 분석으로 표시", async () => {
    getShadowReportMock.mockResolvedValueOnce(
      makeView({
        missed_winners: [
          { symbol: "MU", date: "2025-08-01", decision: "REJECT", return_60d: 0.45 },
        ],
      }),
    );
    render(<ShadowReportPage />);
    const section = await screen.findByTestId("missed-winners");
    expect(within(section).getByText(/historical analysis/)).toBeInTheDocument();
    expect(within(section).getByText("MU")).toBeInTheDocument();
    expect(within(section).getByText("45.0%")).toBeInTheDocument();
  });

  it("raw markdown은 collapsible(details)로 제공한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView({ daily_markdown: "# Daily\nhello" }));
    render(<ShadowReportPage />);
    const details = await screen.findByTestId("raw-md-details");
    expect(details.tagName.toLowerCase()).toBe("details");
    expect(details).not.toHaveAttribute("open"); // 기본 접힘
  });

  it("집중 경고를 상단에 노출한다", async () => {
    getShadowReportMock.mockResolvedValueOnce(
      makeView({ concentration_warnings: ["BUY 60d 양수 수익이 AVGO에 77% 집중"] }),
    );
    render(<ShadowReportPage />);
    const top = await screen.findByTestId("concentration-top");
    expect(within(top).getByText(/AVGO에 77% 집중/)).toBeInTheDocument();
  });

  it("BUY 0이면 'No BUY signals today. Strategy is waiting.' + SKIP/REJECT 요약", async () => {
    getShadowReportMock.mockResolvedValueOnce(
      makeView({ n_buy: 0, buys: [], n_skip: 19, n_reject: 2, riskgate_vetoes: 1 }),
    );
    render(<ShadowReportPage />);
    const empty = await screen.findByTestId("buy-empty-state");
    expect(within(empty).getByText("No BUY signals today. Strategy is waiting.")).toBeInTheDocument();
    expect(within(empty).getByText(/SKIP 19/)).toBeInTheDocument();
  });

  it("real_orders_placed는 항상 0으로 표시된다", async () => {
    getShadowReportMock.mockResolvedValueOnce(makeView());
    render(<ShadowReportPage />);
    await screen.findByTestId("buy-card-NVDA");
    expect(screen.getByText("real_orders_placed = 0")).toBeInTheDocument();
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

  it("백엔드 실패(null) 시 빈 상태로 graceful 처리한다(크래시 없음)", async () => {
    getShadowReportMock.mockResolvedValueOnce(null);
    render(<ShadowReportPage />);
    await waitFor(() =>
      expect(screen.getByText("섀도 리포트가 아직 없습니다.")).toBeInTheDocument(),
    );
  });
});
