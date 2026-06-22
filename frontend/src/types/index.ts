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

/** backend: services/report.py 주간 OHLC + 누적 손익 (일 단위) */
export interface WeeklyBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  cumulative_pnl: number;
}

/** backend: services/report.py 요일별 승률 (월~일) */
export interface DayWinRate {
  /** "월" ~ "일" */
  day: string;
  /** 0 ~ 1 */
  win_rate: number;
}

/** backend: services/report.py 주간 리포트 (③ 주간 거래기록) */
export interface WeeklyReport {
  bars: WeeklyBar[];
  win_rates: DayWinRate[];
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

// --- 목표 플랜 (backend: algorithms/goal_planner.py + services/goal_plan.py + api/goal_plan.py) ---

/** backend PlanMode */
export type PlanMode = "safe" | "aggressive";

/** backend Feasibility */
export type Feasibility = "realistic" | "ambitious" | "unrealistic";

/** backend agents/risk.py RiskLimits (분수: 0.05 = 5%). */
export interface RiskLimits {
  max_risk_pct: number;
  max_drawdown_pct: number;
  max_position_pct: number;
}

/** backend algorithms/goal_planner.py GoalDerivedSettings */
export interface GoalDerivedSettings {
  /** 0.0(보수적) ~ 1.0(공격적) */
  appetite: number;
  risk_limits: RiskLimits;
  stop_loss_atr_multiplier: number;
  feasibility: Feasibility;
  /** 분수 (0.02 = 2%) */
  required_monthly_return: number;
}

/** backend services/goal_plan.py GoalPlan — POST /api/goal-plan 응답 */
export interface GoalPlan {
  settings: GoalDerivedSettings;
  rationale: string;
  summary: string;
  feasibility: Feasibility;
  required_monthly_return: number;
}

/** backend api/goal_plan.py GoalPlanRequest — POST 요청 body */
export interface GoalPlanRequest {
  target_amount: number;
  months: number;
  mode: PlanMode;
  /** 생략 시 backend 포트폴리오 total_equity 사용 */
  current_equity?: number | null;
}

/** backend api/goal_plan.py GoalPlanRecordOut — POST /api/goal-plan/apply 응답(평탄 DTO) */
export interface GoalPlanRecord {
  id: number;
  target_amount: number;
  months: number;
  mode: PlanMode;
  required_monthly_return: number;
  feasibility: Feasibility;
  appetite: number;
  max_risk_pct: number;
  max_drawdown_pct: number;
  max_position_pct: number;
  stop_loss_atr_multiplier: number;
  rationale: string | null;
  applied: boolean;
  created_at: string;
}

// --- 섀도 리포트 view (report-only, /api/shadow) ---
export interface ShadowHealthFinding {
  check: string;
  status: string;
  message: string;
}

export interface ShadowBuy {
  symbol: string;
  decision_date: string | null;
  reason: string;
  shadow_score: number | null;
  momentum_score: number | null;
  volume_ratio_20d: number | null;
  price_above_20ma: boolean | null;
  ma20_above_ma50: boolean | null;
  relative_strength: number | null;
  distance_from_high: number | null;
  riskgate_passed: boolean | null;
  riskgate_reasons: string[];
  riskgate_result: string; // PASS | VETO | N/A
  position_shares: number;
  position_state: string; // held | flat
  is_reentry: boolean | null;
  previous_exit_reason: string | null;
  days_since_last_exit: number | null;
  planned_entry_type: string;
  entry_limit_buffer_pct: number;
  planned_stop_loss: number;
  planned_trailing_stop: number;
  planned_max_holding: number;
  real_orders_placed: number;
}

export interface ShadowOutcomeRow {
  date: string;
  symbol: string;
  decision: string;
  return_60d: number | null;
  scorable: boolean;
}

export interface ShadowReportView {
  available: boolean;
  empty_message: string | null;
  run_command: string;
  health_status: string;
  health_findings: ShadowHealthFinding[];
  report_date: string | null;
  reference_date: string | null;
  selected_date: string | null;
  available_dates: string[];
  n_buy: number;
  n_reject: number;
  n_skip: number;
  riskgate_vetoes: number;
  real_orders_placed: number;
  buys: ShadowBuy[];
  pending_counts: Record<string, number>;
  matured_counts: Record<string, number>;
  recent_outcomes: ShadowOutcomeRow[];
  reentry_total: number;
  reentry_count: number;
  concentration_warnings: string[];
  daily_markdown: string | null;
}

export interface ShadowRunResult {
  ok: boolean;
  returncode: number;
  tail: string;
  real_orders_placed: number;
}
