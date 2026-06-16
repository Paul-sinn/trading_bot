// ② 일간 거래기록 — 오늘 체결 내역 테이블(티커·진입가·청산가·실현손익·AI 메모).
// Server Component. 데이터: getTrades()(REST) 시도 → 실패 시 mockTrades 폴백.
// UI_GUIDE ②: 숫자 컬럼 tabular-nums text-right, 실현손익 컬럼만 시맨틱 색상.
import { Card } from "@/components/ui/Card";
import { getTrades } from "@/lib/api";
import { mockTrades } from "@/lib/mock";
import { formatUsd, pnlColorClass } from "@/lib/utils";

export default async function DailyTradesPage() {
  const trades = (await getTrades()) ?? mockTrades;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <h1 className="text-2xl font-semibold text-white">일간 거래기록</h1>

      <Card>
        {trades.length === 0 ? (
          <div className="py-12 text-center text-sm text-neutral-500">
            오늘 체결 없음
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-neutral-800 text-xs uppercase text-neutral-500">
                <th className="px-3 py-2 text-left font-medium">티커</th>
                <th className="px-3 py-2 text-right font-medium">진입가</th>
                <th className="px-3 py-2 text-right font-medium">청산가</th>
                <th className="px-3 py-2 text-right font-medium">실현손익</th>
                <th className="px-3 py-2 text-left font-medium">AI 메모</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className="border-b border-neutral-800">
                  <td className="px-3 py-3 text-sm font-medium text-white">
                    {t.symbol}
                  </td>
                  <td className="px-3 py-3 text-right text-sm tabular-nums text-neutral-300">
                    {formatUsd(t.entry_price)}
                  </td>
                  <td className="px-3 py-3 text-right text-sm tabular-nums text-neutral-300">
                    {t.exit_price === null ? "—" : formatUsd(t.exit_price)}
                  </td>
                  <td
                    className={`px-3 py-3 text-right text-sm tabular-nums ${pnlColorClass(
                      t.realized_pnl,
                    )}`}
                  >
                    {formatUsd(t.realized_pnl, true)}
                  </td>
                  <td className="px-3 py-3 text-sm text-neutral-300">
                    {t.ai_memo}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
