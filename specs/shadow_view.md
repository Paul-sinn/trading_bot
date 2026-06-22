# SPEC: shadow_view (UI 섀도 리포트 연결 — report-only)

기존 UI(Next.js frontend + FastAPI backend)에 "섀도 리포트" 뷰를 추가해 report-only 산출물(reports/*)을
보여준다. 새 대시보드를 만들지 않고 기존 nav/페이지/REST 패턴을 재사용한다. 아키텍처 규칙대로 frontend는
backend REST(`/api/shadow`)만 호출하고 거래소/LLM/파일을 직접 만지지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스 API 미연결.
스캐너/디시전/사이징/RiskGate·진입/청산/유니버스·베이스라인 미변경(읽기 전용).

## 입력 파일 (reports/)
- daily_shadow_report.md, shadow_health_check.json, signal_decision_log.jsonl, decision_outcome_score.jsonl.

## backend (backend/app)
- service `services/shadow_report.py`: 파일을 읽어 `ShadowReportView`(pydantic)로 변환. 파일 없음 →
  available=False + 안내 메시지. malformed JSONL/JSON → 건너뜀/UNKNOWN(크래시 없음).
- route `api/shadow.py`: `GET /api/shadow` → view. `POST /api/shadow/run` → 고정 커맨드
  `python -m experiments.daily_shadow_report`만 실행(클라이언트 인자 없음, 주문 경로 없음). main.py 등록.

## view model (ShadowReportView)
- available, empty_message, run_command, health_status(PASS/WARN/FAIL/UNKNOWN), health_findings.
- report_date, reference_date, n_buy/n_reject/n_skip, riskgate_vetoes, real_orders_placed(=0).
- buys(detailed pre-trade review — 아래), pending_counts/matured_counts(per horizon), recent_outcomes(60d),
  reentry_total/count, concentration_warnings, daily_markdown(raw).
- available_dates: 결정 로그에 존재하는 거래일(내림차순) — 과거 BUY 예시 리뷰용 날짜 선택지.
- selected_date: 현재 보고 있는 날짜(요청 date 또는 최신).

## BUY 사전검토 / 주문 계획 상세 (BuyView — report-only)
각 BUY 후보마다 "라이브였다면 이렇게 주문했을 것"을 서술만 한다(실행 없음). 필드:
- symbol, decision_date, reason(요약).
- shadow_score, momentum_score, volume_ratio_20d (있으면; 없으면 null 안전).
- price_above_20ma, ma20_above_ma50 (추세 플래그; 있으면), relative_strength, distance_from_high.
- riskgate_passed + riskgate_reasons + riskgate_result(PASS|VETO|N/A).
- position_shares + position_state(held|flat).
- is_reentry, previous_exit_reason, days_since_last_exit (결과 원장 reentry 컨텍스트에서 (date,symbol) 매칭; 없으면 null).
- planned_entry_type=next-bar-limit, entry_limit_buffer_pct=0.03, planned_stop_loss=0.15,
  planned_trailing_stop=0.20, planned_max_holding=60 (잠긴 베이스라인을 '서술'만 — 변경 아님).
- real_orders_placed=0 (항상).

## 날짜 선택 / 과거 BUY 예시 리뷰 (report-only)
- `GET /api/shadow?date=YYYY-MM-DD`: 결정 로그를 해당 날짜로 필터해 읽기만 한다(원장 미변경, 실행 없음).
- `POST /api/shadow/run {date?}`: 고정 커맨드 + 엄격 검증된 `--date YYYY-MM-DD`만 전달. 러너는 ID 멱등
  append(중복 원장 행 없음). 베이스라인/유니버스 미변경. 잘못된 날짜 형식은 거부.

## 리뷰 화면 폴리시 (기능/전략 변경 아님 — 가독성·결과 연결·오해 방지)
- 결과 연결(OutcomeDetailView): 각 BUY/결정에 forward 결과를 (date,symbol)로 머지 — returns 1/5/10/20/60d,
  MFE/MAE, stop_hit/trail_hit/time_close, mature(60d 성숙), scorable. 미성숙 horizon은 "pending", 없으면 "n/a".
- historical vs live-forward: record_mode. 결정일이 원장 frontier(최신일)보다 과거면 `historical`,
  최신일이면 `live-forward`. historical은 실거래처럼 보이면 안 됨 — "Historical simulation record — not a live
  trade." 표기. 모든 레코드는 report-only(real_orders_placed=0).
- 포지션 상태: held/flat은 '시뮬/report-only 포지션 상태'로 라벨. 실보유 암시 금지.
- 계획 수량: 0/null/미상이면 "not sized / report-only"로 표시(0.0000을 실행 가능 수량처럼 보이지 않게).
- 리뷰 필터(decisions_detail, 선택 날짜): BUY/REJECT/SKIP only, 재진입 only, best/worst 60d, pending. 빈 데이터
  안전.
- missed-winner(missed_winners, historical 분석): REJECT/SKIP인데 이후 60d 강한 수익. 데이터 없으면 비표시.
  전략 변경 아님 — 과거 분석 라벨.
- 집중 경고: 상단에도 노출 + top 심볼 기여(가능 시). 하단 raw md와 별개.
- raw md: collapsible(접기/펴기). 기본 접힘으로 페이지 지배 방지.
- 빈 상태: BUY 0 → "No BUY signals today. Strategy is waiting." / 성숙 결과 없음 → "Outcomes pending." /
  파일 없음·malformed → 크래시 없음.

## 추가 view 필드
- buys[].outcome: OutcomeDetailView|None, buys[].record_mode.
- decisions_detail: list[DecisionDetailView] (선택 날짜 전체 결정 + 결과, 필터용).
- missed_winners: list[MissedWinnerView] (symbol,date,decision,return_60d — 전 기간 historical 분석).
- has_mature_outcomes: bool, latest_ledger_date: str|None.

## frontend (frontend/src)
- nav에 "섀도 리포트"(`/shadow`) 추가. page `app/shadow/page.tsx`: `/api/shadow` fetch → 헬스 배지,
  카운트, BUY 사전검토 상세, pending/matured, 최근 결과, 재진입, 집중 경고, real_orders=0, raw md.
- BUY ≥ 1: 각 BUY마다 사전검토 카드(시그널 지표 + RiskGate + 재진입 + 주문 계획 상세).
  "report-only 주문 계획" 섹션: 주문 버튼/브로커/Robinhood 동작 없음 + "This is a simulated plan only" 명시.
- BUY 0: "No BUY signals today. Strategy is waiting." + 아래에 SKIP/REJECT 요약.
- 날짜 선택 드롭다운(available_dates) → `?date=` 로 과거 BUY 예시 읽기. "이 날짜로 재생성" → `run {date}`.
- 파일 없으면 친절한 빈 상태 + 실행 안내(`python -m experiments.daily_shadow_report`). 크래시 금지.
- "일간 섀도 리포트 재생성" 버튼 → `POST /api/shadow/run`(report-only).

## 테스트 (tests/test_shadow_report.py)
- 파일 없음 → 빈 상태(크래시 없음), malformed JSONL/JSON 안전 처리.
- 헬스 PASS/WARN/FAIL 표시, real_orders_placed=0(위반 시 노출), 카운트/BUY/pending·matured/재진입.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스, 새 대시보드, 인증/배포.
