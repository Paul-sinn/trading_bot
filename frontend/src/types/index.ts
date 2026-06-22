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

export interface ShadowOutcomeDetail {
  scorable: boolean;
  mature: boolean;
  return_1d: number | null;
  return_5d: number | null;
  return_10d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  mfe: number | null;
  mae: number | null;
  stop_hit: boolean | null;
  trail_hit: boolean | null;
  time_close: boolean | null;
}

export interface ShadowDecisionDetail {
  symbol: string;
  decision: string; // BUY | REJECT | SKIP
  date: string | null;
  riskgate_result: string;
  is_reentry: boolean | null;
  position_state: string;
  record_mode: string; // historical | live-forward
  outcome: ShadowOutcomeDetail | null;
}

export interface ShadowMissedWinner {
  symbol: string;
  date: string;
  decision: string; // REJECT | SKIP
  return_60d: number;
}

export interface ShadowBuy {
  symbol: string;
  decision_date: string | null;
  reason: string;
  record_mode: string; // historical | live-forward
  outcome: ShadowOutcomeDetail | null;
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
  latest_ledger_date: string | null;
  has_mature_outcomes: boolean;
  buys: ShadowBuy[];
  decisions_detail: ShadowDecisionDetail[];
  missed_winners: ShadowMissedWinner[];
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

/** backend: services/live_session.py LiveSessionState. 라이브 자동매매 세션 상태(실주문 0). */
export type TradingMode = "report_only" | "live_auto";

export interface LiveSessionState {
  automation_running: boolean;
  trading_mode: TradingMode;
  session_id: string | null;
  started_at: string | null;
  stopped_at: string | null;
  stop_reason: string | null;
  emergency_halt: boolean;
  live_enabled: boolean;
  broker_connected: boolean;
  last_heartbeat: string | null;
  real_orders_placed: number;
  daily_order_count: number;
  current_exposure: number;
  // 라이브 시장데이터 + report_only 스캔 루프 상태(모니터링 전용 — 주문 없음).
  market_data_provider: string;
  market_data_status: string;
  live_scan_running: boolean;
  last_scan_at: string | null;
  last_scan_event_count: number;
  latest_buy_candidates: string[];
  // Mock LLM 의사결정 파이프라인 상태(무비용 — ai_cost_estimate_today는 항상 0.0).
  latest_candidates: LiveCandidate[];
  latest_order_intents: OrderIntent[];
  ai_calls_today: number;
  ai_cost_estimate_today: number;
  llm_provider: string;
  llm_budget_status: string;
  latest_review_at: string | null;
}

/** backend: services/llm_review.py ReviewResult. mock LLM 리뷰(cost 0.00). */
export interface LlmReviewResult {
  symbol: string;
  decision: "approve" | "veto" | "needs_review";
  confidence: number;
  reason: string;
  risk_notes: string;
  can_reduce_notional: boolean;
  max_notional_override_usd: number | null;
  cost_usd: number;
  provider_name: string;
}

/** backend: services/candidate_pipeline.py Candidate. */
export interface LiveCandidate {
  key: string;
  scan_event_key: string;
  session_id: string | null;
  symbol: string;
  date: string;
  strategy_id: string;
  price: number | null;
  status:
    | "queued"
    | "reviewed"
    | "vetoed"
    | "approved"
    | "needs_review"
    | "blocked_by_execution_gate";
  review: LlmReviewResult | null;
  rejection_reasons: string[];
  block_reason: "AI_BUDGET_EXCEEDED" | "LLM_COOLDOWN_ACTIVE" | null;
  created_at: string;
  reviewed_at: string | null;
}

/** backend: services/execution_gate.py OrderIntent. dry-run only — real_orders_placed=0. */
export interface OrderIntent {
  timestamp: string;
  session_id: string | null;
  trading_mode: string;
  strategy_id: string;
  symbol: string;
  side: string;
  scan_event_key: string;
  mock_llm_decision: string;
  mock_llm_confidence: number;
  mock_llm_reason: string;
  execution_gate_status: "accepted_dry_run" | "rejected";
  rejection_reasons: string[];
  planned_order_type: string;
  planned_limit_price: number | null;
  planned_notional_usd: number | null;
  planned_quantity: number | null;
  real_orders_placed: number;
  broker_order_id: null;
  status: string;
}

/** backend: services/candidate_pipeline.py AiStatus. */
export interface AiStatus {
  llm_provider: string;
  ai_calls_today: number;
  ai_cost_estimate_today: number;
  ai_budget_remaining: number;
  max_llm_calls_per_day: number;
  max_llm_cost_usd_per_day: number;
  cooldown_seconds_per_symbol: number;
  llm_budget_status: string;
  latest_review_at: string | null;
  last_review_by_symbol: Record<string, string>;
}

/** backend: services/live_scan.py ScanEvent. report_only 스캔 결과(real_orders_placed=0). */
export type ScanStatus =
  | "BUY_CANDIDATE"
  | "REJECT"
  | "SKIP"
  | "INSUFFICIENT_DATA"
  | "ERROR";

export interface LiveScanEvent {
  timestamp: string;
  session_id: string | null;
  trading_mode: string;
  provider: string;
  symbol: string;
  price: number | null;
  scan_status: ScanStatus;
  reason: string;
  features: Record<string, unknown>;
  riskgate_status: string | null;
  buy_candidate: boolean;
  real_orders_placed: number;
}

/** backend: services/live_session.py LiveActionResult. start/stop/halt 결과(status로 분기). */
export interface LiveActionResult {
  status: string;
  state: LiveSessionState;
  real_orders_placed: number;
}

/** backend: services/live_records.py LiveDailyRecord. */
export interface LiveDailyRecord {
  date: string;
  session_ids: string[];
  started_at: string | null;
  stopped_at: string | null;
  orders_submitted: number;
  orders_filled: number;
  orders_cancelled: number;
  realized_pnl: number;
  unrealized_pnl: number | null;
  win_rate: number | null;
  max_drawdown_intraday: number | null;
  stop_reason: string | null;
  notes: string;
  real_orders_placed: number;
}

/** backend: services/broker_snapshot.py BrokerSnapshot. read-only 워커 적재 — real_orders_placed=0. */
export interface BrokerSnapshot {
  provider: string;
  timestamp: string;
  source: string;
  account_last4: string;
  total_value: number | null;
  cash: number | null;
  buying_power: number | null;
  positions: Record<string, unknown>[];
  open_orders: Record<string, unknown>[];
  quotes: Record<string, unknown>[];
  errors: string[];
  real_orders_placed: number;
}

/** backend: services/order_receipt.py OrderReceipt. dry-run 영수증 — broker_order_id null, real_order_placed false. */
export interface OrderReceipt {
  receipt_id: string;
  timestamp: string;
  source: string;
  mode: string;
  intent_id: string;
  idempotency_key: string;
  symbol: string;
  side: string;
  quantity: number | null;
  limit_price: number | null;
  notional: number | null;
  decision: "WOULD_SUBMIT" | "BLOCKED" | "SKIPPED" | "ERROR";
  reason: string;
  broker_order_id: null;
  real_order_placed: boolean;
  real_orders_placed: number;
  control_flags_checked: boolean;
  broker_snapshot_checked: boolean;
  errors: string[];
}

/** backend: services/real_order_executor.py ExecutionStatus. 실주문 scaffold — 기본 비활성, real_orders_placed=0. */
export interface ExecutionStatus {
  real_execution_enabled: boolean;
  require_manual_arm: boolean;
  agentic_account_only: boolean;
  arm_status: string; // missing | disarmed | expired | armed
  arm_expires_at: string | null;
  max_notional_per_real_order_usd: number;
  max_real_orders_per_day: number;
  real_orders_today: number;
  // 프로덕션 준비도: environment=production·실 시장시간 영수증만 반영.
  latest_decision: string | null;
  latest_block_reason: string | null;
  latest_environment: string | null;
  // test/proof(mocked 시장시간) 이력은 별도 카운트로만 — 프로덕션 latest와 섞이지 않음.
  test_proof_count: number;
  real_orders_placed: number;
}

/** backend: services/position_manager.py Position. broker 스냅샷 기반(읽기 전용). */
export interface BrokerPosition {
  symbol: string;
  quantity: number;
  average_buy_price: number | null;
  current_quote: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  peak_price: number | null;
  entry_source: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  holding_days: number | null;
  status: "open" | "missing" | "manually_closed_detected";
}

/** backend: services/position_manager.py ExitDecision. dry-run 청산 — broker_order_id null, real_order_placed false. */
export interface ExitDecision {
  timestamp: string;
  symbol: string;
  quantity: number;
  average_buy_price: number | null;
  current_price: number | null;
  unrealized_pnl_pct: number | null;
  exit_signal:
    | "HOLD"
    | "STOP_LOSS"
    | "TRAILING_STOP"
    | "TIME_STOP"
    | "MANUAL_CLOSE_DETECTED"
    | "ERROR";
  reason: string;
  would_sell_quantity: number;
  broker_order_id: null;
  real_order_placed: boolean;
  real_orders_placed: number;
}

/** backend: services/live_records.py LiveWeeklyRecord. */
export interface LiveWeeklyRecord {
  week_start: string;
  week_end: string;
  trading_days: number;
  total_orders: number;
  filled_orders: number;
  realized_pnl: number;
  win_rate: number | null;
  max_daily_loss: number;
  notes: string;
}
