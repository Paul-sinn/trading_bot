// ① 대시보드 — 포트폴리오 요약 / 리스크% 게이지 / 봇 토글 / 실시간 티커.
// Server Component 기본. 실시간·인터랙션(티커·토글)만 Client Component로 분리.
// 데이터: getPortfolio()(REST) 시도 → 실패 시 mock 폴백 (백엔드 없이도 graceful).
import { Card } from "@/components/ui/Card";
import { Gauge } from "@/components/ui/Gauge";
import { BotToggle } from "@/components/dashboard/BotToggle";
import { LiveTicker } from "@/components/dashboard/LiveTicker";
import { getPortfolio } from "@/lib/api";
import { mockPortfolio, mockTrades } from "@/lib/mock";
import { formatUsd, pnlColorClass } from "@/lib/utils";

// 청산된 거래(exit_price != null) 중 수익(realized_pnl > 0) 비율(%). 0건 → 0.
function winRate(): number {
  const closed = mockTrades.filter((t) => t.exit_price !== null);
  if (closed.length === 0) return 0;
  const wins = closed.filter((t) => t.realized_pnl > 0).length;
  return (wins / closed.length) * 100;
}

// 포지션 노출 비율(%) = (총자산 − 현금) / 총자산 × 100. 실시간 리스크% 게이지 값.
function exposurePct(totalEquity: number, cash: number): number {
  if (totalEquity <= 0) return 0;
  return ((totalEquity - cash) / totalEquity) * 100;
}

export default async function DashboardPage() {
  const portfolio = (await getPortfolio()) ?? mockPortfolio;
  const wr = winRate();
  const risk = exposurePct(portfolio.total_equity, portfolio.cash);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">대시보드</h1>
        <BotToggle initialOn={true} />
      </div>

      {/* 포트폴리오 요약: 총자산 / 오늘 손익 / 승률 */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="space-y-1">
          <div className="text-sm font-medium text-neutral-400">총자산</div>
          <div className="text-3xl font-semibold tabular-nums text-white">
            {formatUsd(portfolio.total_equity)}
          </div>
        </Card>
        <Card className="space-y-1">
          <div className="text-sm font-medium text-neutral-400">오늘 손익</div>
          <div
            className={`text-3xl font-semibold tabular-nums ${pnlColorClass(
              portfolio.day_pnl,
            )}`}
          >
            {formatUsd(portfolio.day_pnl, true)}
          </div>
        </Card>
        <Card className="space-y-1">
          <div className="text-sm font-medium text-neutral-400">승률</div>
          <div className="text-3xl font-semibold tabular-nums text-white">
            {wr.toFixed(1)}%
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 실시간 리스크% 게이지 */}
        <Card className="space-y-4">
          <div className="text-sm font-medium text-neutral-400">
            실시간 리스크
          </div>
          <Gauge value={risk} label="리스크" />
        </Card>

        {/* 실시간 가격 티커 */}
        <Card className="space-y-3">
          <div className="text-sm font-medium text-neutral-400">
            실시간 가격
          </div>
          <LiveTicker />
        </Card>
      </div>
    </div>
  );
}
