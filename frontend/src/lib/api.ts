// backend REST fetch 래퍼. 외부 거래소/AI는 절대 직접 호출하지 않고
// backend(SSOT)만 호출한다 (CLAUDE.md CRITICAL / ADR-001).
import type { Portfolio, Trade, WeeklyReport } from "@/types";

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
