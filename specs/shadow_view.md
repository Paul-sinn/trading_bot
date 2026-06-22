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
- buys(planned entry/exit), pending_counts/matured_counts(per horizon), recent_outcomes(60d),
  reentry_total/count, concentration_warnings, daily_markdown(raw).

## frontend (frontend/src)
- nav에 "섀도 리포트"(`/shadow`) 추가. page `app/shadow/page.tsx`: `/api/shadow` fetch → 헬스 배지,
  카운트, BUY 표, pending/matured, 최근 결과, 재진입, 집중 경고, real_orders=0, raw md.
- 파일 없으면 친절한 빈 상태 + 실행 안내(`python -m experiments.daily_shadow_report`). 크래시 금지.
- "일간 섀도 리포트 재생성" 버튼 → `POST /api/shadow/run`(report-only).

## 테스트 (tests/test_shadow_report.py)
- 파일 없음 → 빈 상태(크래시 없음), malformed JSONL/JSON 안전 처리.
- 헬스 PASS/WARN/FAIL 표시, real_orders_placed=0(위반 시 노출), 카운트/BUY/pending·matured/재진입.

## 비범위
- 실제 주문/체결, 스캐너/디시전/RiskGate/베이스라인 변경, LLM/뉴스, 새 대시보드, 인증/배포.
