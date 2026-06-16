// 백엔드 없이도 페이지가 렌더되도록 하는 결정론적 mock 데이터.
// backend MockPortfolioProvider / ws ticker mock 값과 일치시킨다.
// 후속 step에서 실제 api.ts / ws.ts 호출로 교체 가능하게 둔다.
import type {
  Goals,
  MarketDirection,
  Portfolio,
  RiskProfile,
  TickerMessage,
  Trade,
} from "@/types";

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
