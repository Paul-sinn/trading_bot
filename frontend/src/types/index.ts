// 백엔드 스키마(Pydantic)와 수동 동기화. 변경 시 backend/app/services·ws와 맞춘다.

/** backend: services/portfolio.py Position */
export interface Position {
  symbol: string;
  quantity: number;
  avg_buy_price: number;
  current_price: number;
}

/** backend: services/portfolio.py Portfolio */
export interface Portfolio {
  total_equity: number;
  cash: number;
  positions: Position[];
  day_pnl: number;
}

/** backend: ws/ticker.py 단일 심볼 quote */
export interface TickerQuote {
  price: number;
  ts: string;
}

/** backend: ws/ticker.py ticker_snapshot 메시지 */
export interface TickerMessage {
  type: "ticker";
  data: Record<string, TickerQuote>;
}

// 이후 페이지(거래기록/시황/목표/성향)용 타입 — 현재는 mock 데이터와 동기화한다.

export interface Trade {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  realized_pnl: number;
  ai_memo: string;
  closed_at: string | null;
}

export type DirectionLabel = "bullish" | "neutral" | "bearish";

export interface MarketDirection {
  date: string;
  summary: string;
  label: DirectionLabel;
  rationale: string;
}

export interface Goals {
  target_amount: number;
  current_amount: number;
  max_drawdown_pct: number;
  max_position_pct: number;
}

export interface RiskProfile {
  /** 0(보수적) ~ 100(공격적) */
  risk_appetite: number;
  sector_whitelist: string[];
  sector_blacklist: string[];
}
