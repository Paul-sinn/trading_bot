"use client";

// 섀도 리포트 — report-only 전진 검증 산출물(reports/*) 뷰.
// backend /api/shadow만 호출(거래소/LLM 직접 호출 금지). 주문 절대 없음(real_orders_placed = 0).
// 파일 없으면 친절한 빈 상태 + 실행 안내. 절대 크래시하지 않는다.
import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { getShadowReport, runDailyShadow } from "@/lib/api";
import type { ShadowReportView } from "@/types";

function healthBadge(status: string): string {
  if (status === "PASS") return "bg-emerald-900/40 text-emerald-300 border-emerald-700";
  if (status === "WARN") return "bg-amber-900/40 text-amber-300 border-amber-700";
  if (status === "FAIL") return "bg-red-900/40 text-red-300 border-red-700";
  return "bg-neutral-800 text-neutral-300 border-neutral-700";
}

export default function ShadowReportPage() {
  const [view, setView] = useState<ShadowReportView | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setView(await getShadowReport());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onRun = useCallback(async () => {
    setRunning(true);
    setRunMsg(null);
    const res = await runDailyShadow();
    setRunMsg(
      res
        ? `재생성 ${res.ok ? "성공" : "실패"} (real_orders_placed=${res.real_orders_placed})`
        : "재생성 호출 실패(백엔드 확인)",
    );
    setRunning(false);
    await load();
  }, [load]);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">섀도 리포트 (report-only)</h1>
        <button
          onClick={onRun}
          disabled={running}
          className="rounded-lg border border-neutral-700 bg-[#1a1a1a] px-3 py-2 text-sm text-neutral-200 hover:text-white disabled:opacity-50"
        >
          {running ? "재생성 중…" : "일간 섀도 리포트 재생성"}
        </button>
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

          <Card>
            <div className="mb-2 text-sm font-semibold text-white">오늘 BUY (planned entry/exit — report-only)</div>
            {view.buys.length === 0 ? (
              <div className="py-6 text-center text-sm text-neutral-500">오늘 BUY 없음</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                    <th className="px-3 py-2 text-left">티커</th>
                    <th className="px-3 py-2 text-left">진입</th>
                    <th className="px-3 py-2 text-left">청산</th>
                    <th className="px-3 py-2 text-right">보유</th>
                  </tr>
                </thead>
                <tbody>
                  {view.buys.map((b) => (
                    <tr key={b.symbol} className="border-b border-neutral-800">
                      <td className="px-3 py-2 font-medium text-white">{b.symbol}</td>
                      <td className="px-3 py-2 text-neutral-300">
                        {b.planned_entry_type} (buf {(b.entry_limit_buffer_pct * 100).toFixed(0)}%)
                      </td>
                      <td className="px-3 py-2 text-neutral-300">
                        stop {(b.planned_stop_loss * 100).toFixed(0)}% / trail{" "}
                        {(b.planned_trailing_stop * 100).toFixed(0)}% / {b.planned_max_holding}d
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-neutral-300">
                        {b.position_shares.toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

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
