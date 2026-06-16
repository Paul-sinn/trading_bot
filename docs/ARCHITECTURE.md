# 아키텍처

## 디렉토리 구조
```
trading-bot/
├── frontend/              # Next.js 14 (App Router) + Tailwind + shadcn/ui + Recharts
│   └── src/
│       ├── app/           # 6개 페이지 + API 라우트 (BFF 프록시)
│       ├── components/    # UI 컴포넌트 (shadcn/ui 기반)
│       ├── lib/           # 유틸 + WebSocket 클라이언트 + fetch 래퍼
│       └── types/         # TypeScript 타입 (백엔드 스키마와 동기화)
├── backend/               # FastAPI + WebSocket + SQLite + Redis
│   ├── app/
│   │   ├── main.py        # FastAPI 진입점, 라우터 등록
│   │   ├── api/           # REST 라우트 (포트폴리오, 주문, 설정, 리포트)
│   │   ├── ws/            # WebSocket 엔드포인트 (실시간 티커/포트폴리오 push)
│   │   ├── db/            # SQLAlchemy 모델 + 세션 (SQLite→PostgreSQL)
│   │   ├── cache/         # Redis 래퍼 (가격 캐시, 레이트리밋)
│   │   └── core/          # 설정, 시크릿 로딩, 의존성
├── agents/                # 서브에이전트 6개 (Python)
│   ├── base.py            # 공통 에이전트 인터페이스 + 라이프사이클
│   ├── scanner.py         # 스캐너 에이전트
│   ├── decision.py        # 판단 에이전트 (Claude API)
│   ├── executor.py        # 실행 에이전트 (Robinhood MCP)
│   ├── risk.py            # 리스크 에이전트 (kill-switch)
│   ├── reporter.py        # 리포트 에이전트
│   └── notifier.py        # 알림 에이전트 (슬랙/SMS)
├── algorithms/            # 시그널/필터/사이징 (Python + TA-lib + Pandas)
│   ├── signals.py         # Layer 1: EMA/RSI/MACD
│   ├── filters.py         # Layer 2: 거래량/ATR/센티먼트/VIX
│   └── sizing.py          # Layer 3: Kelly + 스탑로스 + 성향 가중치
├── specs/                 # 기능별 SDD 스펙 문서 (기능명.md)
├── tests/                 # 기능별 TDD 테스트 (test_기능명.py)
├── docs/                  # PRD / ARCHITECTURE / ADR / UI_GUIDE
├── .claude/               # Claude Code hooks (PreToolUse / PostToolUse)
└── .env                   # 시크릿 (Robinhood, Claude API 키 등) — git 제외
```

## 패턴
- **백엔드가 단일 진실 공급원(SSOT)**: 모든 외부 API(Robinhood MCP, Claude)·DB 접근은 backend에서만. frontend는 backend REST/WS만 호출한다.
- **에이전트 = 독립 루프**: 각 에이전트는 `base.Agent` 인터페이스(`start/stop/tick`)를 구현하고 자기 주기로 실행. 리스크 에이전트가 다른 에이전트의 kill-switch를 보유.
- **알고리즘 = 순수 함수**: `algorithms/`는 입력(가격 DataFrame, 설정)→출력(시그널/사이즈)인 부수효과 없는 순수 함수. 테스트 용이성 최우선.
- **프론트엔드**: Server Components 기본, 실시간/인터랙션 영역(티커, 토글, 슬라이더)만 Client Component.

## 데이터 흐름
```
[자동매매 루프]
스캐너(1분) → 시그널 후보 → 알고리즘 3레이어(signals→filters→sizing)
  → 통과 종목 → 판단 에이전트(Claude) → 매수/홀드/매도
  → [PreToolUse hook: 리스크 에이전트 체크] → 실행 에이전트(MCP 주문)
  → 체결 → DB 저장 → WebSocket push → UI

[실시간 동기화]
Robinhood MCP → backend(Redis 캐시) → WebSocket → frontend (1초 갱신)

[리스크 차단]
리스크 에이전트(실시간 리스크% 계산) → 한도 초과 → kill-switch(전 에이전트 정지)
  → 알림 에이전트(슬랙/SMS)

[수동 거래]
UI 티커 검색 → 수량 입력 → backend REST → [리스크 체크] → MCP 주문 → 체결 → UI

[AI 시황 — 매일 9시]
스케줄러 → 판단 에이전트(Claude 시황 요약 + 7일 방향성) → DB → UI 카드
```

## 상태 관리
- **서버 상태(권위)**: SQLite(개발)/PostgreSQL(프로덕션). 거래기록·설정·리포트·시황.
- **실시간 캐시**: Redis — 최신 가격/포트폴리오 스냅샷, 에이전트 간 공유 상태, 레이트리밋.
- **프론트 서버 상태**: REST fetch(초기 로드) + WebSocket(실시간 갱신). 전역 클라이언트 상태 라이브러리는 도입하지 않고 React state + WS 구독으로 처리.
- **봇 ON/OFF & kill-switch 상태**: backend가 권위. UI 토글은 backend 상태를 반영만 한다.

## 통신 규약
- REST: 초기 데이터 로드, 설정 변경, 수동 주문, 리포트 조회.
- WebSocket: 가격 티커, 포트폴리오 스냅샷, 봇 상태, 알림 이벤트의 서버→클라이언트 push.
- frontend↔backend 타입은 `frontend/src/types`와 backend 스키마(Pydantic)를 수동 동기화한다.
