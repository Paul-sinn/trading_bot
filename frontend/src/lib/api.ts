// backend REST fetch 래퍼. 외부 거래소/AI는 절대 직접 호출하지 않고
// backend(SSOT)만 호출한다 (CLAUDE.md CRITICAL / ADR-001).
import type {
  GoalPlan,
  GoalPlanRecord,
  GoalPlanRequest,
  MarketDirection,
  Portfolio,
  ShadowReportView,
  ShadowRunResult,
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
