// 백엔드 없이도 페이지가 렌더되도록 하는 결정론적 mock 데이터.
// backend MockPortfolioProvider / ws ticker mock 값과 일치시킨다.
// 후속 step에서 실제 api.ts / ws.ts 호출로 교체 가능하게 둔다.
import type {
  GoalPlan,
  Goals,
  LiveSessionState,
  MarketDirection,
  Portfolio,
  RiskProfile,
  TickerMessage,
  Trade,
  WeeklyReport,
} from "@/types";

// 백엔드 없이도 대시보드가 렌더되도록 하는 안전 기본 라이브 상태(정지 + 브로커 미연결).
// 실제 상태는 getLiveStatus()로 받는다. mock은 "자동화 꺼짐"이 기본(매매 시작 안 함).
export const mockLiveStatus: LiveSessionState = {
  automation_running: false,
  trading_mode: "report_only",
  session_id: null,
  started_at: null,
  stopped_at: null,
  stop_reason: null,
  emergency_halt: false,
  live_enabled: false,
  broker_connected: false,
  last_heartbeat: null,
  real_orders_placed: 0,
  daily_order_count: 0,
  current_exposure: 0,
  market_data_provider: "mock",
  market_data_status: "available",
  live_scan_running: false,
  last_scan_at: null,
  last_scan_event_count: 0,
  latest_buy_candidates: [],
};

export const mockPortfolio: Portfolio = {
  // backend: cash 5000 + AAPL(10*195) + TSLA(5*240) = 8150
  total_equity: 8150,
  cash: 5000,
  day_pnl: -50,
  positions: [
    { symbol: "AAPL", quantity: 10, avg_buy_price: 190, current_price: 195 },
    { symbol: "TSLA", quantity: 5, avg_buy_price: 250, current_price: 240 },
  ],
};

// backend ws/ticker.py 기본 워치리스트(AAPL/TSLA/NVDA)와 기준가에 맞춘 mock 스냅샷.
// WS 미연결 시 티커가 비지 않도록 초기/폴백 값으로 사용한다.
export const mockTicker: TickerMessage = {
  type: "ticker",
  data: {
    AAPL: { price: 195, ts: "2026-06-16T13:30:00Z" },
    TSLA: { price: 240, ts: "2026-06-16T13:30:00Z" },
    NVDA: { price: 120, ts: "2026-06-16T13:30:00Z" },
  },
};

export const mockTrades: Trade[] = [
  {
    id: "t1",
    symbol: "AAPL",
    side: "buy",
    entry_price: 190,
    exit_price: 195,
    quantity: 10,
    realized_pnl: 50,
    ai_memo: "EMA 9/21 골든크로스 + 거래량 급등 확인.",
    closed_at: "2026-06-16T14:30:00Z",
  },
  {
    id: "t2",
    symbol: "TSLA",
    side: "sell",
    entry_price: 250,
    exit_price: 240,
    quantity: 5,
    realized_pnl: -50,
    ai_memo: "RSI 과매수 + 부정 뉴스 센티먼트로 청산.",
    closed_at: "2026-06-16T15:10:00Z",
  },
];

// 7거래일 OHLC + 누적 손익(우측 축). 한 down 데이를 포함한 완만한 상승 추세.
export const mockWeekly: WeeklyReport = {
  bars: [
    { date: "06-08", open: 100, high: 103, low: 99, close: 102, cumulative_pnl: 20 },
    { date: "06-09", open: 102, high: 104, low: 101, close: 103, cumulative_pnl: 35 },
    { date: "06-10", open: 103, high: 103, low: 98, close: 99, cumulative_pnl: -10 },
    { date: "06-11", open: 99, high: 102, low: 98, close: 101, cumulative_pnl: 25 },
    { date: "06-12", open: 101, high: 106, low: 100, close: 105, cumulative_pnl: 60 },
    { date: "06-13", open: 105, high: 107, low: 104, close: 106, cumulative_pnl: 80 },
    { date: "06-14", open: 106, high: 108, low: 103, close: 104, cumulative_pnl: 55 },
  ],
  win_rates: [
    { day: "월", win_rate: 0.6 },
    { day: "화", win_rate: 0.5 },
    { day: "수", win_rate: 0.3 },
    { day: "목", win_rate: 0.7 },
    { day: "금", win_rate: 0.8 },
    { day: "토", win_rate: 0.66 },
    { day: "일", win_rate: 0.4 },
  ],
};

export const mockDirection: MarketDirection = {
  date: "2026-06-16",
  summary: "기술주 중심 완만한 반등, 변동성은 평균 수준 유지.",
  label: "neutral",
  rationale: "VIX 안정 + 금리 동결 기대. 단기 방향성은 제한적.",
};

export const mockGoals: Goals = {
  target_amount: 10000,
  current_amount: 6850,
  max_drawdown_pct: 15,
  max_position_pct: 20,
};

export const mockRiskProfile: RiskProfile = {
  risk_appetite: 50,
  sector_whitelist: ["Technology", "Healthcare"],
  sector_blacklist: ["Energy"],
};

// 백엔드 미가동 시 AI 분석 패널이 보여줄 graceful fallback 계획.
// backend MockGoalPlanProvider + derive_settings(SAFE, 완만한 목표) 결과와 형태/단위(분수)를 맞춘다.
// 수치는 백엔드 응답을 표시만 하는 예시값 — 프론트에서 재계산하지 않는다(ADR-003/005).
export const mockGoalPlan: GoalPlan = {
  settings: {
    appetite: 0.11,
    risk_limits: {
      max_risk_pct: 0.008,
      max_drawdown_pct: 0.06,
      max_position_pct: 0.12,
    },
    stop_loss_atr_multiplier: 1.72,
    feasibility: "realistic",
    required_monthly_return: 0.0172,
  },
  rationale:
    "월 1.7% 필요, 모드 safe, 실현가능성 realistic → appetite 0.11, risk 0.8%. (백엔드 미연결 — 예시 계획)",
  summary: "필요 월 수익률 1.7%, 실현가능성 realistic, 최대 리스크 0.8%.",
  feasibility: "realistic",
  required_monthly_return: 0.0172,
};
