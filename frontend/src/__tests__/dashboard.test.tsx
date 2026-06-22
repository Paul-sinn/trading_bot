import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { mockLiveStatus, mockPortfolio } from "@/lib/mock";

// 네트워크/WS 의존성 제거: 백엔드 없이도 페이지가 렌더되는지(graceful) 검증한다.
// CRITICAL: start/stop/halt는 vi.fn() — 렌더(=UI 새로고침)가 이들을 호출하지 않음을 검증한다.
const startLive = vi.fn();
const stopLive = vi.fn();
const emergencyHalt = vi.fn();
const getScanEvents = vi.fn().mockResolvedValue([]);
const getCandidates = vi.fn().mockResolvedValue([]);
const getOrderIntents = vi.fn().mockResolvedValue([]);
const getAiStatus = vi.fn().mockResolvedValue(null);
const getBrokerSnapshot = vi.fn().mockResolvedValue(null);
const getOrderReceipts = vi.fn().mockResolvedValue([]);
const getExecutionStatus = vi.fn().mockResolvedValue(null);
const getPositions = vi.fn().mockResolvedValue([]);
const getExits = vi.fn().mockResolvedValue([]);
vi.mock("@/lib/api", () => ({
  getPortfolio: vi.fn().mockResolvedValue(mockPortfolio),
  getLiveStatus: vi.fn().mockResolvedValue(mockLiveStatus),
  getScanEvents: (...args: unknown[]) => getScanEvents(...args),
  getCandidates: (...args: unknown[]) => getCandidates(...args),
  getOrderIntents: (...args: unknown[]) => getOrderIntents(...args),
  getAiStatus: (...args: unknown[]) => getAiStatus(...args),
  getBrokerSnapshot: (...args: unknown[]) => getBrokerSnapshot(...args),
  getOrderReceipts: (...args: unknown[]) => getOrderReceipts(...args),
  getExecutionStatus: (...args: unknown[]) => getExecutionStatus(...args),
  getPositions: (...args: unknown[]) => getPositions(...args),
  getExits: (...args: unknown[]) => getExits(...args),
  startLive: (...args: unknown[]) => startLive(...args),
  stopLive: (...args: unknown[]) => stopLive(...args),
  emergencyHalt: (...args: unknown[]) => emergencyHalt(...args),
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

  it("라이브 제어 버튼(거래 시작/정지/비상 정지)을 렌더한다", async () => {
    render(await DashboardPage());
    expect(
      screen.getByRole("button", { name: "거래 시작" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "거래 정지" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "비상 정지" }),
    ).toBeInTheDocument();
  });

  it("자동화·브로커 연결 상태를 표시한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("자동화")).toBeInTheDocument();
    expect(screen.getByText("브로커 연결")).toBeInTheDocument();
  });

  it("시장데이터 provider·라이브 스캔 상태를 표시한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("시장데이터")).toBeInTheDocument();
    expect(screen.getByText("라이브 스캔")).toBeInTheDocument();
    // report_only 모니터링 라벨(실주문 없음)
    expect(
      screen.getByText("report_only monitoring — no real orders"),
    ).toBeInTheDocument();
  });

  it("Mock LLM 파이프라인 라벨과 AI 비용 $0.00을 표시한다", async () => {
    render(await DashboardPage());
    expect(
      screen.getByText("Mock LLM only — no paid API, no real orders"),
    ).toBeInTheDocument();
    expect(screen.getByText(/AI cost = \$0\.00/)).toBeInTheDocument();
  });

  it("브로커 스냅샷 패널(read-only 라벨)을 렌더하고 스냅샷 없으면 경고를 표시한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("브로커 스냅샷")).toBeInTheDocument();
    expect(
      screen.getByText("read-only broker snapshot — no orders"),
    ).toBeInTheDocument();
    // null 스냅샷 → "없음" 경고
    expect(screen.getByText(/브로커 스냅샷 없음/)).toBeInTheDocument();
  });

  it("워커 주문 영수증 패널(dry-run only 라벨)을 렌더한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("워커 주문 영수증")).toBeInTheDocument();
    expect(
      screen.getByText("dry-run receipt only — no real order submitted"),
    ).toBeInTheDocument();
  });

  it("실주문 실행 준비 패널(scaffold 비활성 + 프로덕션 준비도 라벨)을 렌더한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("실주문 실행 준비")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Real order execution is disabled. Scaffold only — no Robinhood order submitted.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Production readiness only uses real market-hours receipts.",
      ),
    ).toBeInTheDocument();
  });

  it("포지션/청산 매니저 패널(dry-run only 라벨)을 렌더한다", async () => {
    render(await DashboardPage());
    expect(screen.getByText("포지션 / 청산 매니저")).toBeInTheDocument();
    expect(
      screen.getByText("Exit manager is dry-run only — no sell order submitted."),
    ).toBeInTheDocument();
  });

  it("렌더(=새로고침)는 절대 매매를 시작하거나 주문을 내지 않는다", async () => {
    render(await DashboardPage());
    expect(startLive).not.toHaveBeenCalled();
    expect(stopLive).not.toHaveBeenCalled();
    expect(emergencyHalt).not.toHaveBeenCalled();
  });

  it("실시간 가격 티커를 mock으로라도 표시한다(백엔드 없이 graceful)", async () => {
    render(await DashboardPage());
    // mockTicker의 기본 워치리스트 심볼 중 하나가 보여야 한다.
    expect(screen.getByText("AAPL")).toBeInTheDocument();
  });
});
