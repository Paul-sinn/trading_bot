# config/ — 정책 드래프트 (DRAFT, 코드에 미연결)

이 디렉토리는 **선언적 정책 config 드래프트**다. 전략/주문/실행 코드는 아직 이 파일들을 읽지 않는다
(미연결 — wiring은 별도 검증 단계). 데이터일 뿐 로직이 아니다.

- `universe_tiers.json` — 6티어 + 각 티커 status(approved/watch/needs_review/reject/data_missing) +
  ADV/ATR/타입/leveraged/data_coverage. 출처: Norgate 실데이터(`scripts/audit_universe.py`).
- `risk_profiles.json` — 리스크 모드 B/C, Concentration Phase 1~4, Tier5 프로파일, 포트폴리오 가드,
  RiskGate 하드 veto, 재진입 규칙. 미정 수치는 `data_missing` / `todos`로 표시.

## 원칙
- **Source of truth**: `docs/overnight_constitution_and_universe_audit.md`(+ `docs/STRATEGY.md`, `docs/UNIVERSE_TIERS.md`).
  충돌 시 헌장(STRATEGY.md)이 우선. 이 config가 헌장을 덮어쓰지 않는다.
- ⚠️ **universe audit이지 live greenlight가 아니다.** 자동 라이브 주문 코드 없음.
- 값 튜닝 금지(이번 단계). `data_missing`/`todos`는 후속 확정 대상.
- 검증: `tests/test_config_universe.py`(정책 형식·일관성만 검증, 성과 검증 아님).
