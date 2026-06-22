# SPEC: Zero-cost Mock LLM Decision Pipeline (live BUY_CANDIDATE → dry-run OrderIntent)

> report_only 라이브 스캔의 `BUY_CANDIDATE`를 **무비용 mock** 의사결정 파이프라인으로 처리해
> **dry-run OrderIntent**를 만든다. 구조 검증 전용. **실 LLM API·Robinhood·브로커·실주문 없음.**
> AI 비용 `ai_cost_estimate=0.00`, `real_orders_placed=0` 항상.

파이프라인: `Live Scan BUY_CANDIDATE → CandidateQueue → MockLLMReviewProvider → ExecutionGate(dry-run)
→ OrderIntent → UI`.

## 불변식 (CRITICAL)
- 실주문 없음(`real_orders_placed=0`), 브로커/Robinhood 호출 없음, 실 LLM API 호출/키 읽기 없음.
- AI 비용 0.00. mock 호출은 `ai_calls_today`에 카운트되지만 `cost_usd=0.0`.
- 잠긴 베이스라인(stop 0.15/trail 0.20/max-hold 60/next-bar-limit)·기본 유니버스·scanner/decision/
  sizing/RiskGate·Shadow·Norgate·라이브 스캔 동작 미변경.
- OrderIntent는 주문이 아니다. Robinhood 호출 안 함, `real_orders_placed` 증가 안 함.

## 1. CandidateQueue
- `scan_status == BUY_CANDIDATE`만 처리.
- 멱등 dedupe 키: `session_id | symbol | date | strategy_id`.
- 쿨다운: 심볼당 `MIN_LLM_COOLDOWN_SECONDS_PER_SYMBOL`(900s) 내 재리뷰 차단(`LLM_COOLDOWN_ACTIVE`).
- candidate 상태: `queued / reviewed / vetoed / approved / needs_review / blocked_by_execution_gate`.

## 2. MockLLMReviewProvider
- `LLMReviewProvider` Protocol + `MockLLMReviewProvider`. 외부 API/키 읽기 없음, 결정론.
  `provider_name=mock_llm`, `cost_usd=0.00`.
- 출력: symbol, decision(`approve|veto|needs_review`), confidence, reason, risk_notes,
  can_reduce_notional, max_notional_override_usd(있다면 **cap 이하만** — 리스크 상향 불가),
  cost_usd, provider_name. **RiskGate/ExecutionGate를 우회/무력화 못 한다.**
- 결정론 로직: ERROR/INSUFFICIENT_DATA → veto; non-BUY_CANDIDATE → veto(비-BUY 승인 금지);
  features 불완전(relative_strength/rsi/regime 누락) → needs_review; 그 외 강한 BUY → approve.
- `LLM_PROVIDER=mock` 기본. 알 수 없는 provider → fail-closed(실 LLM 경로 없음).

## 3. AI 예산/쿨다운 셸 (무비용)
- `ai_calls_today`, `ai_cost_estimate_today`(0.00), `ai_budget_remaining`, `last_review_by_symbol`,
  `cooldown_seconds_per_symbol`.
- Config: `LLM_PROVIDER=mock`, `MAX_LLM_CALLS_PER_DAY=50`, `MAX_LLM_COST_USD_PER_DAY=5.00`,
  `MIN_LLM_COOLDOWN_SECONDS_PER_SYMBOL=900`.
- mock 호출은 ai_calls_today 카운트, 비용 0.00. 콜 한도 초과 → `AI_BUDGET_EXCEEDED`. 쿨다운 활성 →
  `LLM_COOLDOWN_ACTIVE`. 실 LLM 경로 없음.

## 4. ExecutionGate (dry-run)
- 출력: `accepted_dry_run / rejected` + `rejection_reasons`. 브로커/Robinhood/실주문/실자금 이동 없음.
- 체크: trading_mode report_only(또는 dry-run 호환); source decision=BUY_CANDIDATE; mock LLM=approve;
  symbol ∈ 베이스라인 유니버스; OrderIntent 중복 아님; quantity 유한 & >0(계산 시); limit price 유한;
  `MAX_NOTIONAL_PER_ORDER` 준수; `MAX_DAILY_ORDER_INTENTS` 준수; `MAX_TOTAL_INTENDED_EXPOSURE` 준수;
  emergency_halt false; automation_running true; `real_orders_placed`는 0 유지.

## 5. OrderIntent → `reports/live_order_intents.jsonl`
필드: timestamp, session_id, trading_mode, strategy_id, symbol, side=BUY, scan_event_key,
mock_llm_decision, mock_llm_confidence, mock_llm_reason, execution_gate_status, rejection_reasons,
planned_order_type=limit, planned_limit_price, planned_notional_usd, planned_quantity,
real_orders_placed=0, broker_order_id=null, status=DRY_RUN_INTENT_ONLY.
- accepted_dry_run인 approve 후보만 OrderIntent 기록. veto/needs_review/gate-rejected → 기록 안 함.

## 6. API (읽기 전용)
- `GET /api/live/candidates?limit=50`, `GET /api/live/order-intents?limit=50`, `GET /api/ai/status`.
- `GET /api/live/status` 확장: latest_candidates, latest_order_intents, ai_calls_today,
  ai_cost_estimate_today, llm_provider, llm_budget_status, latest_review_at.
- 읽기 전용 엔드포인트는 매매/스캔 시작·LLM 호출·OrderIntent 생성·주문을 절대 하지 않는다.

## 7. 라이브 루프 통합
- 스캔이 BUY_CANDIDATE를 내면: 큐 적재 → mock 리뷰 → approve를 ExecutionGate(dry-run) 통과 →
  candidate/review/order-intent 로그 기록 → 대시보드 상태 갱신.
- Stop / Emergency-Halt: 스캔 루프·candidate 처리 중지, 신규 mock 리뷰·OrderIntent 차단,
  포지션 청산 안 함, 브로커 호출 안 함.

## 8. 대시보드
최근 BUY 후보 + mock LLM 결과/confidence/reason + ExecutionGate dry-run 결과 + planned
limit/quantity/notional + AI calls today + AI cost = $0.00 + 라벨 "Mock LLM only — no paid API, no
real orders" + real_orders_placed=0.

## 엣지케이스
- reports/ 부재 → 쓰기 전 생성. jsonl 손상/부재 → 빈 상태 안전 처리.
- price 0/None → quantity 계산 불가 → gate가 reject(유한·양수 아님).
- 알 수 없는 provider → fail-closed.
