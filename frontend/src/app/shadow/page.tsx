"use client";

// 섀도 리포트 — report-only 전진 검증 산출물(reports/*) 뷰.
// backend /api/shadow만 호출(거래소/LLM 직접 호출 금지). 주문 절대 없음(real_orders_placed = 0).
// 브로커/Robinhood/라이브 주문 경로 없음. 파일 없으면 친절한 빈 상태 + 실행 안내. 절대 크래시하지 않는다.
import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { getShadowReport, runDailyShadow } from "@/lib/api";
import type { ShadowBuy, ShadowReportView } from "@/types";

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

const fmtNum = (v: number | null, digits = 2): string =>
  v === null || v === undefined ? "n/a" : v.toFixed(digits);

const fmtPct = (v: number | null, digits = 1): string =>
  v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(digits)}%`;

const fmtFlag = (v: boolean | null): string =>
  v === null || v === undefined ? "n/a" : v ? "✓" : "✗";

// BUY 1건의 사전검토 + report-only 주문 계획 카드.
function PreTradeReviewCard({ buy }: { buy: ShadowBuy }) {
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
        <span className={`rounded-md border px-2 py-0.5 text-xs font-medium ${gateBadge(buy.riskgate_result)}`}>
          RiskGate: {buy.riskgate_result}
        </span>
        <span className="rounded-md border border-neutral-700 bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300">
          포지션: {buy.position_state} ({buy.position_shares.toFixed(4)})
        </span>
        {buy.is_reentry && (
          <span className="rounded-md border border-sky-800 bg-sky-900/30 px-2 py-0.5 text-xs text-sky-300">
            재진입
          </span>
        )}
      </div>

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

      {/* RiskGate 사유(veto 시) */}
      {buy.riskgate_reasons.length > 0 && (
        <div className="text-sm text-red-300">
          RiskGate: {buy.riskgate_reasons.join("; ")}
        </div>
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
          <li>계획 수량: {buy.position_shares.toFixed(4)}</li>
        </ul>
        <div className="mt-3 text-xs text-emerald-300">
          real_orders_placed = {buy.real_orders_placed} · 브로커/Robinhood/라이브 주문 없음
        </div>
      </div>
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
            <div className="text-neutral-500">
              {view?.empty_message ?? "먼저 실행하세요:"}
            </div>
            <code className="inline-block rounded bg-[#0a0a0a] px-2 py-1 text-neutral-300">
              python -m experiments.daily_shadow_report
            </code>
          </div>
        </Card>
      )}

      {!loading && view && view.available && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-md border px-3 py-1 text-sm font-medium ${healthBadge(view.health_status)}`}>
              데이터 헬스: {view.health_status}
            </span>
            <span className="text-sm text-neutral-400">report date: {view.report_date ?? "—"}</span>
            {view.selected_date && (
              <span className="rounded-md border border-sky-800 bg-sky-900/30 px-3 py-1 text-sm text-sky-300">
                과거 예시 리뷰: {view.selected_date}
              </span>
            )}
            <span className="rounded-md border border-emerald-800 bg-emerald-900/30 px-3 py-1 text-sm text-emerald-300">
              real_orders_placed = {view.real_orders_placed}
            </span>
          </div>

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

          {/* BUY 사전검토 / 주문 계획 상세 — report-only */}
          <div className="space-y-3">
            <div className="text-sm font-semibold text-white">
              BUY 사전검토 / 주문 계획 (report-only)
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
            ) : (
              view.buys.map((b) => <PreTradeReviewCard key={b.symbol} buy={b} />)
            )}
          </div>

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

          {view.concentration_warnings.length > 0 && (
            <Card className="border-amber-800">
              <div className="mb-2 text-sm font-semibold text-amber-300">집중 경고</div>
              <ul className="space-y-1 text-sm text-amber-200">
                {view.concentration_warnings.map((w, i) => (
                  <li key={i}>⚠️ {w}</li>
                ))}
              </ul>
            </Card>
          )}

          {view.daily_markdown && (
            <Card>
              <div className="mb-2 text-sm font-semibold text-white">일간 섀도 리포트 (raw)</div>
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-neutral-400">
                {view.daily_markdown}
              </pre>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
