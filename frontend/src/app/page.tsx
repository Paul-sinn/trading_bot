// ① 대시보드 — 포트폴리오 요약 / 리스크% 게이지 / 봇 토글 / 실시간 티커.
// Server Component 기본. 실시간·인터랙션(티커·토글)만 Client Component로 분리.
// 데이터: getPortfolio()(REST) 시도 → 실패 시 mock 폴백 (백엔드 없이도 graceful).
import { Card } from "@/components/ui/Card";
import { Gauge } from "@/components/ui/Gauge";
import { BrokerSnapshotPanel } from "@/components/dashboard/BrokerSnapshot";
import { LiveControls } from "@/components/dashboard/LiveControls";
import { LiveTicker } from "@/components/dashboard/LiveTicker";
import { OrderReceiptsPanel } from "@/components/dashboard/OrderReceipts";
import { ExecutionReadinessPanel } from "@/components/dashboard/ExecutionReadiness";
import { PositionExitManagerPanel } from "@/components/dashboard/PositionExitManager";
import { SellExecutionReadinessPanel } from "@/components/dashboard/SellExecutionReadiness";
import {
  getBrokerSnapshot,
  getCandidates,
  getExecutionStatus,
  getExits,
  getLiveStatus,
  getOrderIntents,
  getOrderReceipts,
  getPortfolio,
  getPositions,
  getScanEvents,
  getSellExecutionStatus,
} from "@/lib/api";
import { mockLiveStatus, mockPortfolio, mockTrades } from "@/lib/mock";
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
  // 읽기 전용 fetch만 한다. 페이지 로드/새로고침은 절대 매매를 시작하지 않는다(매매 시작은
  // LiveControls의 "거래 시작" 버튼 클릭 전용). 백엔드 없으면 mock(정지) 폴백.
  const portfolio = (await getPortfolio()) ?? mockPortfolio;
  const liveStatus = (await getLiveStatus()) ?? mockLiveStatus;
  const scanEvents = (await getScanEvents(50)) ?? [];
  const candidates = (await getCandidates(50)) ?? [];
  const orderIntents = (await getOrderIntents(50)) ?? [];
  const brokerSnapshot = await getBrokerSnapshot(); // null이면 패널이 "없음" 경고 표시
  const orderReceipts = (await getOrderReceipts(50)) ?? [];
  const executionStatus = await getExecutionStatus(); // null이면 패널이 비활성으로 표시
  const sellExecutionStatus = await getSellExecutionStatus();
  const positions = (await getPositions()) ?? [];
  const exits = (await getExits(50)) ?? [];
  const wr = winRate();
  const risk = exposurePct(portfolio.total_equity, portfolio.cash);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">대시보드</h1>
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

      {/* 라이브 트레이딩 세션 제어: 시작/정지/비상정지 + 자동화·브로커 상태 */}
      <Card className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium text-neutral-400">
            라이브 트레이딩
          </div>
          <div className="text-xs text-neutral-600">
            실주문 경로 없음 · Robinhood MCP 어댑터 경계
          </div>
        </div>
        <LiveControls
          initialStatus={liveStatus}
          initialScanEvents={scanEvents}
          initialCandidates={candidates}
          initialOrderIntents={orderIntents}
        />
      </Card>

      {/* 브로커 스냅샷(read-only 워커 브리지) — 잔고/포지션/미체결, 주문 경로 없음 */}
      <BrokerSnapshotPanel snapshot={brokerSnapshot} />

      {/* 워커 주문 영수증(dry-run only) — 실주문 없음, broker_order_id null */}
      <OrderReceiptsPanel receipts={orderReceipts} />

      {/* 포지션 / 청산 매니저(dry-run) — 매도 주문 없음 */}
      <PositionExitManagerPanel positions={positions} exits={exits} />

      {/* 실주문 실행 준비(scaffold) — 기본 비활성, 실주문 경로 없음 */}
      <ExecutionReadinessPanel status={executionStatus} />

      {/* 수동 매도 실행 준비(scaffold) — 기본 비활성, 매도 경로 없음 */}
      <SellExecutionReadinessPanel status={sellExecutionStatus} />

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
