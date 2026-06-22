"use client";

// 섀도 리포트 — report-only 전진 검증 산출물(reports/*) 리뷰 화면.
// backend /api/shadow만 호출(거래소/LLM 직접 호출 금지). 주문 절대 없음(real_orders_placed = 0).
// 브로커/Robinhood/라이브 주문 경로 없음. 파일 없으면 친절한 빈 상태. 절대 크래시하지 않는다.
// 폴리시: 결과 연결 · historical/live-forward 구분 · report-only 포지션/수량 라벨 · 필터 ·
//         missed-winner(historical 분석) · 상단 집중 경고 · raw md 접기.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/Card";
import { getShadowReport, runDailyShadow } from "@/lib/api";
import type {
  ShadowBuy,
  ShadowDecisionDetail,
  ShadowOutcomeDetail,
  ShadowReportView,
} from "@/types";

function healthBadge(status: string): string {
  if (status === "PASS") return "bg-emerald-900/40 text-emerald-300 border-emerald-700";
  if (status === "WARN") return "bg-amber-900/40 text-amber-300 border-amber-700";
  if (status === "FAIL") return "bg-red-900/40 text-red-300 border-red-700";
  return "bg-neutral-800 text-neutral-300 border-neutral-700";
}

function gateBadge(result: string): string {
  if (result === "PASS") return "bg-emerald-900/40 text-emerald-300 border-emerald-700";
  if (result === "VETO") return "bg-red-900/40 text-red-300 border-red-700";
  return "bg-neutral-800 text-neutral-400 border-neutral-700";
}

const isHistorical = (mode: string): boolean => mode === "historical";

function modeBadge(mode: string): string {
  return isHistorical(mode)
    ? "bg-violet-900/40 text-violet-300 border-violet-700"
    : "bg-sky-900/40 text-sky-300 border-sky-700";
}

const modeLabel = (mode: string): string =>
  isHistorical(mode) ? "historical/backfill" : "live-forward ledger";

const fmtNum = (v: number | null, digits = 2): string =>
  v === null || v === undefined ? "n/a" : v.toFixed(digits);

const fmtPct = (v: number | null, digits = 1): string =>
  v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(digits)}%`;

const fmtFlag = (v: boolean | null): string =>
  v === null || v === undefined ? "n/a" : v ? "✓" : "✗";

// 결과 원장이 없으면 "n/a", 있으나 미성숙 horizon은 "pending".
const retCell = (outcome: ShadowOutcomeDetail | null, v: number | null): string => {
  if (!outcome) return "n/a";
  return v === null || v === undefined ? "pending" : fmtPct(v);
};

// report-only 계획 수량 — 실행 가능한 주식 수량처럼 보이지 않게(0.0000 금지).
const plannedQtyLabel = (): string => "not sized / report-only";

// 결과 연결 행(returns 1/5/10/20/60d + MFE/MAE + stop/trail/time).
function OutcomeRow({ outcome }: { outcome: ShadowOutcomeDetail | null }) {
  if (!outcome) {
    return (
      <div className="rounded-md border border-neutral-800 bg-[#0f0f0f] px-3 py-2 text-sm text-neutral-500">
        forward 결과: n/a (아직 채점 전)
      </div>
    );
  }
  const horizons: [string, number | null][] = [
    ["1d", outcome.return_1d],
    ["5d", outcome.return_5d],
    ["10d", outcome.return_10d],
    ["20d", outcome.return_20d],
    ["60d", outcome.return_60d],
  ];
  return (
    <div className="rounded-md border border-neutral-800 bg-[#0f0f0f] p-3">
      <div className="mb-2 text-xs uppercase text-neutral-500">forward 결과 (report-only)</div>
      <div className="grid grid-cols-5 gap-2">
        {horizons.map(([label, v]) => (
          <div key={label} className="text-center">
            <div className="text-xs text-neutral-500">{label}</div>
            <div
              className={`text-sm tabular-nums ${
                v === null ? "text-amber-400" : v >= 0 ? "text-emerald-300" : "text-red-300"
              }`}
            >
              {retCell(outcome, v)}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-400">
        <span>MFE {fmtPct(outcome.mfe)}</span>
        <span>MAE {fmtPct(outcome.mae)}</span>
        <span>stop {fmtFlag(outcome.stop_hit)}</span>
        <span>trailing {fmtFlag(outcome.trail_hit)}</span>
        <span>time_stop {fmtFlag(outcome.time_close)}</span>
      </div>
    </div>
  );
}

// BUY 1건의 사전검토 + 결과 연결 + report-only 주문 계획 카드.
function PreTradeReviewCard({ buy }: { buy: ShadowBuy }) {
  const historical = isHistorical(buy.record_mode);
  const metrics: [string, string][] = [
    ["shadow", fmtNum(buy.shadow_score, 3)],
    ["momentum", fmtNum(buy.momentum_score, 3)],
    ["volume×", fmtNum(buy.volume_ratio_20d, 2)],
    ["rel.strength", fmtNum(buy.relative_strength, 3)],
    ["dist.from high", fmtPct(buy.distance_from_high)],
    [">20ma / 20>50", `${fmtFlag(buy.price_above_20ma)} / ${fmtFlag(buy.ma20_above_ma50)}`],
  ];
  return (
    <Card data-testid={`buy-card-${buy.symbol}`} className="space-y-4">
      {/* 헤더 */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-lg font-semibold text-white">{buy.symbol}</span>
        <span className="text-sm text-neutral-400">{buy.decision_date ?? "—"}</span>
        <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${modeBadge(buy.record_mode)}`}>
          {modeLabel(buy.record_mode)}
        </span>
        <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${gateBadge(buy.riskgate_result)}`}>
          RiskGate: {buy.riskgate_result}
        </span>
        <span
          data-testid={`position-state-${buy.symbol}`}
          className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300"
        >
          시뮬 포지션(report-only): {buy.position_state}
        </span>
        {buy.is_reentry && (
          <span className="rounded-md border border-sky-800 bg-sky-900/30 px-2 py-0.5 text-xs text-sky-300">
            재진입
          </span>
        )}
      </div>

      {historical && (
        <div
          data-testid={`historical-note-${buy.symbol}`}
          className="rounded-md border border-violet-800/60 bg-violet-950/20 px-3 py-2 text-xs text-violet-200"
        >
          Historical simulation record — not a live trade.
        </div>
      )}

      {buy.reason && <div className="text-sm text-neutral-300">{buy.reason}</div>}

      {/* 시그널 지표 */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {metrics.map(([label, val]) => (
          <div key={label} className="rounded-md border border-neutral-800 bg-[#0f0f0f] px-3 py-2">
            <div className="text-xs uppercase text-neutral-500">{label}</div>
            <div className="mt-0.5 text-sm tabular-nums text-neutral-200">{val}</div>
          </div>
        ))}
      </div>

      {/* 결과 연결 */}
      <OutcomeRow outcome={buy.outcome} />

      {/* RiskGate 사유(veto 시) */}
      {buy.riskgate_reasons.length > 0 && (
        <div className="text-sm text-red-300">RiskGate: {buy.riskgate_reasons.join("; ")}</div>
      )}

      {/* 재진입 컨텍스트 */}
      {buy.is_reentry && (
        <div className="text-sm text-sky-300">
          재진입 — 직전 청산 사유: {buy.previous_exit_reason ?? "n/a"}
          {buy.days_since_last_exit !== null && ` · ${buy.days_since_last_exit}일 경과`}
        </div>
      )}

      {/* report-only 주문 계획 */}
      <div
        data-testid={`order-plan-${buy.symbol}`}
        className="rounded-lg border border-amber-800/60 bg-amber-950/20 p-4"
      >
        <div className="mb-2 flex items-center gap-2">
          <span className="text-sm font-semibold text-amber-300">주문 계획 (report-only)</span>
          <span className="rounded border border-amber-700 bg-amber-900/40 px-2 py-0.5 text-xs text-amber-200">
            This is a simulated plan only
          </span>
        </div>
        <ul className="space-y-1 text-sm text-neutral-300">
          <li>진입: {buy.planned_entry_type} (limit buffer {(buy.entry_limit_buffer_pct * 100).toFixed(0)}%)</li>
          <li>손절: {(buy.planned_stop_loss * 100).toFixed(0)}%</li>
          <li>트레일링: {(buy.planned_trailing_stop * 100).toFixed(0)}%</li>
          <li>최대 보유: {buy.planned_max_holding}일</li>
          <li data-testid={`planned-qty-${buy.symbol}`}>계획 수량: {plannedQtyLabel()}</li>
        </ul>
        <div className="mt-3 text-xs text-emerald-300">
          real_orders_placed = {buy.real_orders_placed} · 브로커/Robinhood/라이브 주문 없음
        </div>
      </div>
    </Card>
  );
}

type FilterKey = "all" | "BUY" | "REJECT" | "SKIP" | "reentry" | "best60" | "worst60" | "pending";

const FILTERS: [FilterKey, string][] = [
  ["all", "전체"],
  ["BUY", "BUY"],
  ["REJECT", "REJECT"],
  ["SKIP", "SKIP"],
  ["reentry", "재진입"],
  ["best60", "best 60d"],
  ["worst60", "worst 60d"],
  ["pending", "pending"],
];

function applyFilter(rows: ShadowDecisionDetail[], filter: FilterKey): ShadowDecisionDetail[] {
  switch (filter) {
    case "BUY":
    case "REJECT":
    case "SKIP":
      return rows.filter((r) => r.decision === filter);
    case "reentry":
      return rows.filter((r) => r.is_reentry === true);
    case "pending":
      return rows.filter((r) => !r.outcome || r.outcome.return_60d === null);
    case "best60":
      return rows
        .filter((r) => r.outcome && r.outcome.return_60d !== null)
        .sort((a, b) => (b.outcome!.return_60d ?? 0) - (a.outcome!.return_60d ?? 0));
    case "worst60":
      return rows
        .filter((r) => r.outcome && r.outcome.return_60d !== null)
        .sort((a, b) => (a.outcome!.return_60d ?? 0) - (b.outcome!.return_60d ?? 0));
    default:
      return rows;
  }
}

function ReviewFilterTable({ rows }: { rows: ShadowDecisionDetail[] }) {
  const [filter, setFilter] = useState<FilterKey>("all");
  const filtered = useMemo(() => applyFilter(rows, filter), [rows, filter]);
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-white">리뷰 필터</span>
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            data-testid={`filter-${key}`}
            onClick={() => setFilter(key)}
            className={`rounded-md border px-2 py-1 text-xs ${
              filter === key
                ? "border-neutral-500 bg-neutral-700 text-white"
                : "border-neutral-700 bg-[#1a1a1a] text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <div data-testid="filter-empty" className="py-6 text-center text-sm text-neutral-500">
          해당 조건의 결정 없음
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                <th className="px-2 py-2 text-left">티커</th>
                <th className="px-2 py-2 text-left">결정</th>
                <th className="px-2 py-2 text-left">RiskGate</th>
                <th className="px-2 py-2 text-center">재진입</th>
                <th className="px-2 py-2 text-right">1d</th>
                <th className="px-2 py-2 text-right">10d</th>
                <th className="px-2 py-2 text-right">60d</th>
                <th className="px-2 py-2 text-left">모드</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={`${r.symbol}-${i}`} className="border-b border-neutral-800">
                  <td className="px-2 py-2 font-medium text-white">{r.symbol}</td>
                  <td className="px-2 py-2 text-neutral-300">{r.decision}</td>
                  <td className="px-2 py-2 text-neutral-400">{r.riskgate_result}</td>
                  <td className="px-2 py-2 text-center text-neutral-400">{r.is_reentry ? "✓" : ""}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-neutral-300">{retCell(r.outcome, r.outcome?.return_1d ?? null)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-neutral-300">{retCell(r.outcome, r.outcome?.return_10d ?? null)}</td>
                  <td className="px-2 py-2 text-right tabular-nums text-neutral-300">{retCell(r.outcome, r.outcome?.return_60d ?? null)}</td>
                  <td className="px-2 py-2 text-xs text-neutral-500">{modeLabel(r.record_mode)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function ShadowReportPage() {
  const [view, setView] = useState<ShadowReportView | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");

  const load = useCallback(async (date?: string) => {
    setLoading(true);
    setView(await getShadowReport(date || null));
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onSelectDate = useCallback(
    async (date: string) => {
      setSelectedDate(date);
      await load(date);
    },
    [load],
  );

  const onRun = useCallback(async () => {
    setRunning(true);
    setRunMsg(null);
    const res = await runDailyShadow(selectedDate || null);
    setRunMsg(
      res
        ? `재생성 ${res.ok ? "성공" : "실패"} (real_orders_placed=${res.real_orders_placed})`
        : "재생성 호출 실패(백엔드 확인)",
    );
    setRunning(false);
    await load(selectedDate || undefined);
  }, [load, selectedDate]);

  const reviewingHistorical =
    view?.report_date != null &&
    view?.latest_ledger_date != null &&
    view.report_date < view.latest_ledger_date;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-white">섀도 리포트 (report-only)</h1>
        <div className="flex items-center gap-2">
          {view?.available && view.available_dates.length > 0 && (
            <select
              data-testid="date-select"
              value={selectedDate}
              onChange={(e) => void onSelectDate(e.target.value)}
              className="rounded-lg border border-neutral-700 bg-[#1a1a1a] px-3 py-2 text-sm text-neutral-200"
            >
              <option value="">최신 거래일</option>
              {view.available_dates.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={onRun}
            disabled={running}
            className="rounded-lg border border-neutral-700 bg-[#1a1a1a] px-3 py-2 text-sm text-neutral-200 hover:text-white disabled:opacity-50"
          >
            {running
              ? "재생성 중…"
              : selectedDate
                ? `${selectedDate} 재생성`
                : "일간 섀도 리포트 재생성"}
          </button>
        </div>
      </div>

      {runMsg && <div className="text-sm text-neutral-400">{runMsg}</div>}

      {loading && <Card><div className="py-12 text-center text-sm text-neutral-500">불러오는 중…</div></Card>}

      {!loading && (!view || !view.available) && (
        <Card>
          <div className="space-y-2 py-12 text-center text-sm text-neutral-400">
            <div>섀도 리포트가 아직 없습니다.</div>
            <div className="text-neutral-500">{view?.empty_message ?? "먼저 실행하세요:"}</div>
            <code className="inline-block rounded bg-[#0a0a0a] px-2 py-1 text-neutral-300">
              python -m experiments.daily_shadow_report
            </code>
          </div>
        </Card>
      )}

      {!loading && view && view.available && (
        <>
          {/* 상단 배지 */}
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-md border px-3 py-1 text-sm font-medium ${healthBadge(view.health_status)}`}>
              데이터 헬스: {view.health_status}
            </span>
            <span className="text-sm text-neutral-400">report date: {view.report_date ?? "—"}</span>
            <span
              data-testid="review-mode-badge"
              className={`rounded-md border px-3 py-1 text-sm font-medium ${
                reviewingHistorical ? modeBadge("historical") : modeBadge("live-forward")
              }`}
            >
              {reviewingHistorical ? "historical/backfill 리뷰" : "live-forward ledger"}
            </span>
            <span className="rounded-md border border-emerald-800 bg-emerald-900/30 px-3 py-1 text-sm text-emerald-300">
              real_orders_placed = {view.real_orders_placed}
            </span>
          </div>

          {/* 집중 경고 — 상단 노출 */}
          {view.concentration_warnings.length > 0 && (
            <Card data-testid="concentration-top" className="border-amber-800 bg-amber-950/20">
              <div className="mb-1 text-sm font-semibold text-amber-300">⚠️ 집중 경고</div>
              <ul className="space-y-1 text-sm text-amber-200">
                {view.concentration_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Card>
          )}

          {view.health_findings.length > 0 && (
            <Card>
              <div className="mb-2 text-sm font-semibold text-white">헬스 findings</div>
              <ul className="space-y-1 text-sm text-neutral-300">
                {view.health_findings.map((f, i) => (
                  <li key={i}>
                    <span className="text-neutral-500">[{f.status}]</span> {f.check}: {f.message}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["BUY", view.n_buy],
              ["REJECT", view.n_reject],
              ["SKIP", view.n_skip],
              ["RiskGate veto", view.riskgate_vetoes],
            ].map(([label, val]) => (
              <Card key={label as string}>
                <div className="text-xs uppercase text-neutral-500">{label}</div>
                <div className="mt-1 text-2xl font-semibold tabular-nums text-white">{val}</div>
              </Card>
            ))}
          </div>

          {/* BUY 사전검토 / 결과 연결 / 주문 계획 — report-only */}
          <div className="space-y-3">
            <div className="text-sm font-semibold text-white">
              BUY 사전검토 / 결과 연결 / 주문 계획 (report-only)
            </div>
            {view.buys.length === 0 ? (
              <Card>
                <div data-testid="buy-empty-state" className="py-8 text-center">
                  <div className="text-base text-neutral-300">
                    No BUY signals today. Strategy is waiting.
                  </div>
                  <div className="mt-2 text-sm text-neutral-500">
                    SKIP {view.n_skip} · REJECT {view.n_reject} · RiskGate veto {view.riskgate_vetoes}
                  </div>
                </div>
              </Card>
            ) : !view.has_mature_outcomes ? (
              <>
                <Card data-testid="outcomes-pending">
                  <div className="py-2 text-center text-sm text-amber-300">
                    Outcomes pending. (forward 결과 성숙 전)
                  </div>
                </Card>
                {view.buys.map((b) => <PreTradeReviewCard key={b.symbol} buy={b} />)}
              </>
            ) : (
              view.buys.map((b) => <PreTradeReviewCard key={b.symbol} buy={b} />)
            )}
          </div>

          {/* 리뷰 필터 */}
          {view.decisions_detail.length > 0 && <ReviewFilterTable rows={view.decisions_detail} />}

          {/* missed-winner — historical 분석(전략 변경 아님) */}
          {view.missed_winners.length > 0 && (
            <Card data-testid="missed-winners" className="border-violet-800/60">
              <div className="mb-1 text-sm font-semibold text-violet-300">
                Missed winners — 과거 분석 (historical analysis, 전략 변경 아님)
              </div>
              <div className="mb-2 text-xs text-neutral-500">
                REJECT/SKIP였으나 이후 60d 수익이 강했던 종목. 측정용 — 베이스라인/디시전 미변경.
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                    <th className="px-3 py-2 text-left">티커</th>
                    <th className="px-3 py-2 text-left">date</th>
                    <th className="px-3 py-2 text-left">결정</th>
                    <th className="px-3 py-2 text-right">60d</th>
                  </tr>
                </thead>
                <tbody>
                  {view.missed_winners.map((m, i) => (
                    <tr key={`${m.symbol}-${m.date}-${i}`} className="border-b border-neutral-800">
                      <td className="px-3 py-2 font-medium text-white">{m.symbol}</td>
                      <td className="px-3 py-2 text-neutral-400">{m.date}</td>
                      <td className="px-3 py-2 text-neutral-300">{m.decision}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-emerald-300">{fmtPct(m.return_60d)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Card>
              <div className="mb-2 text-sm font-semibold text-white">결과 성숙 (BUY 원장)</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                    <th className="px-3 py-2 text-left">horizon</th>
                    <th className="px-3 py-2 text-right">matured</th>
                    <th className="px-3 py-2 text-right">pending</th>
                  </tr>
                </thead>
                <tbody>
                  {["1", "5", "10", "20", "60"].map((h) => (
                    <tr key={h} className="border-b border-neutral-800">
                      <td className="px-3 py-2 text-neutral-300">{h}d</td>
                      <td className="px-3 py-2 text-right tabular-nums text-neutral-300">
                        {view.matured_counts[h] ?? 0}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-amber-300">
                        {view.pending_counts[h] ?? 0}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3 text-sm text-neutral-400">
                재진입: 원장 {view.reentry_total}건 중 {view.reentry_count}건 재진입
              </div>
            </Card>

            <Card>
              <div className="mb-2 text-sm font-semibold text-white">최근 채점 결과</div>
              {view.recent_outcomes.length === 0 ? (
                <div className="py-6 text-center text-sm text-neutral-500">없음</div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                      <th className="px-3 py-2 text-left">date</th>
                      <th className="px-3 py-2 text-left">티커</th>
                      <th className="px-3 py-2 text-left">결정</th>
                      <th className="px-3 py-2 text-right">60d</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.recent_outcomes.map((o, i) => (
                      <tr key={`${o.date}-${o.symbol}-${i}`} className="border-b border-neutral-800">
                        <td className="px-3 py-2 text-neutral-400">{o.date}</td>
                        <td className="px-3 py-2 text-white">{o.symbol}</td>
                        <td className="px-3 py-2 text-neutral-300">{o.decision}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-neutral-300">
                          {o.return_60d === null ? "pending" : `${(o.return_60d * 100).toFixed(1)}%`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>

          {/* raw md — 접기 */}
          {view.daily_markdown && (
            <Card>
              <details data-testid="raw-md-details">
                <summary className="cursor-pointer text-sm font-semibold text-white">
                  일간 섀도 리포트 (raw markdown) — 펼치기/접기
                </summary>
                <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs text-neutral-400">
                  {view.daily_markdown}
                </pre>
              </details>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
