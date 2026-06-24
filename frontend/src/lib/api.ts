// backend REST fetch 래퍼. 외부 거래소/AI는 절대 직접 호출하지 않고
// backend(SSOT)만 호출한다 (CLAUDE.md CRITICAL / ADR-001).
import type {
  AiStatus,
  ApprovalView,
  BrokerPosition,
  BrokerSnapshot,
  ExecutionStatus,
  ExitDecision,
  GoalPlan,
  GoalPlanRecord,
  GoalPlanRequest,
  LiveActionResult,
  LiveCandidate,
  LiveDailyRecord,
  LiveScanEvent,
  LiveSessionState,
  LiveWeeklyRecord,
  OrchestratorStatus,
  OrderIntent,
  OrderReceipt,
  OrderRouterResult,
  OrderRouterStatus,
  MarketDirection,
  Portfolio,
  RegimeStatus,
  SellExecutionStatus,
  ShadowReportView,
  ShadowRunResult,
  TradingMode,
  Trade,
  WeeklyReport,
} from "@/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** backend REST 호출. 실패(네트워크/비정상 응답) 시 null을 반환해 graceful 하게 처리. */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!res.ok) {
      console.error(`API ${path} 실패: ${res.status}`);
      return null;
    }
    return (await res.json()) as T;
  } catch (err) {
    console.error(`API ${path} 네트워크 오류:`, err);
    return null;
  }
}

/** 현재 포트폴리오 스냅샷. 실패 시 null (호출부에서 mock 폴백 가능). */
export function getPortfolio(): Promise<Portfolio | null> {
  return apiFetch<Portfolio>("/api/portfolio");
}

/** 오늘 체결 거래기록. 실패 시 null (호출부에서 mock 폴백 가능). */
export function getTrades(): Promise<Trade[] | null> {
  return apiFetch<Trade[]>("/api/trades/daily");
}

/** 주간 OHLC + 누적 손익 + 요일별 승률. 실패 시 null (호출부에서 mock 폴백 가능). */
export function getWeekly(): Promise<WeeklyReport | null> {
  return apiFetch<WeeklyReport>("/api/trades/weekly");
}

/**
 * 매일 9시 Claude 시황 요약 + 7일 방향성. backend가 Claude를 호출하고 frontend는 결과만 받는다
 * (CLAUDE.md CRITICAL: 프론트는 Claude 직접 호출 금지). 실패 시 null (호출부에서 mock 폴백 가능).
 */
export function getDirection(): Promise<MarketDirection | null> {
  return apiFetch<MarketDirection>("/api/direction");
}

/**
 * 목표 플랜 생성(부수효과 없음). backend가 알고리즘 역산 + Claude 근거를 수행하고 프론트는
 * 결과만 받는다 (CLAUDE.md CRITICAL: 프론트는 Claude/거래소 직접 호출 금지). 실패 시 null
 * (호출부에서 mock 폴백 가능).
 */
export function createGoalPlan(req: GoalPlanRequest): Promise<GoalPlan | null> {
  return apiFetch<GoalPlan>("/api/goal-plan", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/**
 * 섀도 리포트 view(report-only). backend가 reports/ 산출물을 읽어 반환한다(거래소/LLM 미호출).
 * 실패 시 null (호출부에서 빈 상태 처리).
 */
export function getShadowReport(
  date?: string | null,
): Promise<ShadowReportView | null> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return apiFetch<ShadowReportView>(`/api/shadow${qs}`);
}

/**
 * 일간 섀도 리포트 재생성(report-only). backend가 `python -m experiments.daily_shadow_report`(+ 검증된
 * --date)만 실행한다 — 주문 절대 없음. date는 과거 BUY 예시 리뷰용. 실패 시 null.
 */
export function runDailyShadow(
  date?: string | null,
): Promise<ShadowRunResult | null> {
  return apiFetch<ShadowRunResult>("/api/shadow/run", {
    method: "POST",
    body: JSON.stringify({ date: date ?? null }),
  });
}

/**
 * 목표 플랜 적용(영속화). backend가 동일 입력으로 플랜을 재생성해 활성 세팅으로 저장한다
 * (검토 후 적용 원칙 — 적용 전에는 활성 세팅 불변). 실패 시 null (호출부에서 로컬 폴백).
 */
export function applyGoalPlan(
  req: GoalPlanRequest,
): Promise<GoalPlanRecord | null> {
  return apiFetch<GoalPlanRecord>("/api/goal-plan/apply", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// --- 라이브 트레이딩 세션 제어 ---
// CRITICAL: status/daily/weekly는 읽기 전용(주문·매매 시작 없음). 매매 시작은 startLive 버튼만.
// 실주문 경로 없음 — backend가 Robinhood MCP 어댑터를 통하며, 미연동 시 NOT_READY_NO_MCP.

/** 라이브 세션 상태(읽기 전용). 페이지 로드/새로고침이 매매를 시작하지 않는다. 실패 시 null. */
export function getLiveStatus(): Promise<LiveSessionState | null> {
  return apiFetch<LiveSessionState>("/api/live/status");
}

/** 라이브 세션 시작(명시적 버튼 클릭 전용). MCP 없으면 status=NOT_READY_NO_MCP. 실패 시 null. */
export function startLive(
  mode: TradingMode = "report_only",
): Promise<LiveActionResult | null> {
  return apiFetch<LiveActionResult>("/api/live/start", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

/** 라이브 세션 정지 — 즉시 신규 주문 차단(포지션 자동청산 없음). 실패 시 null. */
export function stopLive(
  reason?: string | null,
): Promise<LiveActionResult | null> {
  return apiFetch<LiveActionResult>("/api/live/stop", {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

/** 비상 정지 — emergency_halt + 즉시 신규 주문 차단. 실패 시 null. */
export function emergencyHalt(): Promise<LiveActionResult | null> {
  return apiFetch<LiveActionResult>("/api/live/emergency-halt", {
    method: "POST",
  });
}

/** 일간 라이브 기록(읽기 전용 — 주문 없음). 실패 시 null. */
export function getLiveDaily(
  date?: string | null,
): Promise<LiveDailyRecord | null> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return apiFetch<LiveDailyRecord>(`/api/live/daily-record${qs}`);
}

/** 주간 라이브 기록(일간 집계 — 읽기 전용). 실패 시 null. */
export function getLiveWeekly(): Promise<LiveWeeklyRecord[] | null> {
  return apiFetch<LiveWeeklyRecord[]>("/api/live/weekly-record");
}

/** 최근 라이브 스캔 이벤트(읽기 전용 — 스캔 시작 안 함, 주문 없음). 실패 시 null. */
export function getScanEvents(limit = 50): Promise<LiveScanEvent[] | null> {
  return apiFetch<LiveScanEvent[]>(`/api/live/scan-events?limit=${limit}`);
}

/** 최근 BUY 후보 + mock LLM 리뷰(읽기 전용 — 리뷰/주문 시작 안 함). 실패 시 null. */
export function getCandidates(limit = 50): Promise<LiveCandidate[] | null> {
  return apiFetch<LiveCandidate[]>(`/api/live/candidates?limit=${limit}`);
}

/** 최근 dry-run OrderIntent(읽기 전용 — 주문 아님). 실패 시 null. */
export function getOrderIntents(limit = 50): Promise<OrderIntent[] | null> {
  return apiFetch<OrderIntent[]>(`/api/live/order-intents?limit=${limit}`);
}

/** 워커가 쓴 dry-run 주문 영수증 목록(읽기 전용 — MCP 미호출, 주문 없음). 실패 시 null. */
export function getOrderReceipts(limit = 50): Promise<OrderReceipt[] | null> {
  return apiFetch<OrderReceipt[]>(`/api/live/order-receipts?limit=${limit}`);
}

/** 실주문 실행 준비 상태(읽기 전용 — scaffold, 기본 비활성, 주문 없음). 실패 시 null. */
export function getExecutionStatus(): Promise<ExecutionStatus | null> {
  return apiFetch<ExecutionStatus>("/api/live/execution-status");
}

/** 수동 매도 실행 준비 상태(읽기 전용 — scaffold, 기본 비활성, 매도 없음). 실패 시 null. */
export function getSellExecutionStatus(): Promise<SellExecutionStatus | null> {
  return apiFetch<SellExecutionStatus>("/api/live/sell-execution-status");
}

/** Discord 승인 요청 목록 + 실효 상태(읽기 전용 — jsonl만, Discord/Robinhood 미호출). 실패 시 null. */
export function getApprovals(limit = 50): Promise<ApprovalView[] | null> {
  return apiFetch<ApprovalView[]>(`/api/live/approvals?limit=${limit}`);
}

/** 가장 최근 Discord 승인 요청 + 실효 상태(읽기 전용). 실패 시 null. */
export function getLatestApproval(): Promise<ApprovalView | null> {
  return apiFetch<ApprovalView>("/api/live/approvals/latest");
}

/** 자동 주문 라우터 설정 + 일일 카운트(읽기 전용 — 선택/승인 실행 안 함). 실패 시 null. */
export function getOrderRouterStatus(): Promise<OrderRouterStatus | null> {
  return apiFetch<OrderRouterStatus>("/api/live/order-router/status");
}

/** 가장 최근 라우터 결정(읽기 전용 — jsonl만). 실패 시 null. */
export function getLatestRouterDecision(): Promise<OrderRouterResult | null> {
  return apiFetch<OrderRouterResult>("/api/live/order-router/latest");
}

/** 장중 오케스트레이터 상태(읽기 전용 — 승인 요청만, 주문 없음). 실패 시 null. */
export function getOrchestratorStatus(): Promise<OrchestratorStatus | null> {
  return apiFetch<OrchestratorStatus>("/api/live/orchestrator/status");
}

/** 최신 스캔 레짐 요약(읽기 전용 — VIX 폴백/위험축소). 실패 시 null. */
export function getRegimeStatus(): Promise<RegimeStatus | null> {
  return apiFetch<RegimeStatus>("/api/live/regime");
}

// --- 포지션 / 청산 매니저(read-only, dry-run) ---
// CRITICAL: backend는 MCP 미호출(워커 스냅샷만 읽음). 청산은 dry-run — 매도 주문 없음.

/** broker 스냅샷 기반 포지션(읽기 전용 — 주문 없음). 실패 시 null. */
export function getPositions(): Promise<BrokerPosition[] | null> {
  return apiFetch<BrokerPosition[]>("/api/positions");
}

/** 최근 dry-run 청산 판단 목록(읽기 전용 — 매도 없음). 실패 시 null. */
export function getExits(limit = 50): Promise<ExitDecision[] | null> {
  return apiFetch<ExitDecision[]>(`/api/exits?limit=${limit}`);
}

/** AI 예산/쿨다운 상태(읽기 전용 — LLM 호출 없음, cost 0.00). 실패 시 null. */
export function getAiStatus(): Promise<AiStatus | null> {
  return apiFetch<AiStatus>("/api/ai/status");
}

// --- 브로커 스냅샷(read-only 워커 브리지) ---
// CRITICAL: backend는 MCP를 직접 호출하지 않고 워커가 적재한 reports/broker_snapshots.jsonl만 읽는다.
// 주문 경로 없음 — real_orders_placed=0. 프론트는 backend REST만 호출한다.

/** 최신 브로커 스냅샷(읽기 전용 — MCP 호출 없음). 없거나 실패 시 null. */
export function getBrokerSnapshot(): Promise<BrokerSnapshot | null> {
  return apiFetch<BrokerSnapshot>("/api/broker/snapshot");
}

/** 최근 브로커 스냅샷 목록(읽기 전용 — MCP 호출 없음). 실패 시 null. */
export function getBrokerSnapshots(
  limit = 50,
): Promise<BrokerSnapshot[] | null> {
  return apiFetch<BrokerSnapshot[]>(`/api/broker/snapshots?limit=${limit}`);
}
