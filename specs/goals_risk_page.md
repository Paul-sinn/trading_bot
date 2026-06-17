# SPEC: goals_risk_page (⑤ 목표 & 리스크)

Phase 2-frontend Step 5. 프론트엔드 ⑤ 목표 & 리스크(`frontend/src/app/goals/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 6 — 목표금액 진행, Layer 3 사이징·리스크 한도),
UI_GUIDE(⑤ 목표 & 리스크, 게이지/진행 바 규칙, 입력 필드 스타일, 설정 페이지 max-w-3xl),
ARCHITECTURE(백엔드 SSOT), ADR-001(프론트/백 분리), `agents/risk.py`(RiskLimits).

CRITICAL: 설정값을 frontend에서 직접 거래 로직에 적용하지 않는다. 저장 권위는 backend.
UI는 입력/표시까지만 한다(로컬 상태 반영, 저장은 후속 step에서 backend 연동).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/Gauge/타입/mock)을 재사용한다.

## 표시 요소 (UI_GUIDE ⑤ 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| 목표금액 진행 바 | 현재/목표 대비 진행률(진행 바 프리미티브 Gauge, 퍼센트 병기) | `Goals.current_amount`, `.target_amount` |
| 드로우다운 한도 입력 | 당일 드로우다운 한도(%) 입력 필드 | `Goals.max_drawdown_pct` → `RiskLimits.max_drawdown_pct` |
| 최대 포지션 크기 입력 | 단일 포지션 노출 한도(%) 입력 필드 | `Goals.max_position_pct` → `RiskLimits.max_position_pct` |

### 목표금액 진행 바
- 진행률(%) = `current_amount / target_amount × 100`. `target_amount <= 0`이면 0.
- 진행 바는 step 0 `Gauge` 프리미티브를 재사용한다(퍼센트 병기, 트랙 bg-neutral-800).
- 현재/목표 금액을 `formatUsd`로 병기 표시한다.

### 리스크 한도 설정 입력
- 드로우다운 한도(%), 최대 포지션 크기(%) 두 개의 숫자 입력 필드.
- 입력 스타일(UI_GUIDE): `rounded-lg bg-[#1a1a1a] border border-neutral-800 px-4 py-3`.
- 값은 `agents/risk.py`의 `RiskLimits.max_drawdown_pct`, `.max_position_pct`와 매핑한다.
- 입력 변경은 로컬 상태로만 반영한다(저장은 backend — 후속 연동).

## 데이터 로딩 규칙
- 기본값은 `mockGoals`(step 0). 입력 인터랙션이 있으므로 Client Component로 구현한다.
- 설정값을 거래 로직에 적용하지 않는다(표시/입력까지만 — backend 권위).

## 타입
- `Goals`: `{ target_amount, current_amount, max_drawdown_pct, max_position_pct }`.
- `RiskLimits`(agents/risk.py): `max_risk_pct`, `max_drawdown_pct`, `max_position_pct`.

## 엣지케이스
- `target_amount <= 0` → 진행률 0%, 크래시 없음.
- 입력 비움/비정상 값 → 로컬 상태 갱신(검증/저장은 backend 책임, 이 step 범위 밖).
