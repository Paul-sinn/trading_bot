# SPEC: profile_settings_page (⑥ 투자성향 설정)

Phase 2-frontend Step 6. 프론트엔드 ⑥ 투자성향 설정(`frontend/src/app/profile/page.tsx`)의
표시 요소·데이터 소스·상태를 정의한다.

관련 문서: PRD(핵심기능 6 — 투자성향 반영, 슬라이더 변경 시 포지션 크기·스탑로스 재계산),
UI_GUIDE(⑥ 투자성향 설정, 슬라이더 규칙: 공격적↔보수적 양 끝 라벨, 입력/토글 스타일, 설정 페이지 max-w-3xl),
ARCHITECTURE(백엔드 SSOT), ADR-001(프론트/백 분리), `algorithms/sizing.py`(risk_appetite_weight / position_size).

CRITICAL: 슬라이더/토글 값을 frontend에서 직접 매매 파라미터에 적용하지 않는다. 저장 권위는 backend.
UI는 입력/미리보기까지만 한다(로컬 상태 반영, 저장은 후속 step에서 backend 연동).
새 디자인 토큰/프리미티브를 만들지 않고 step 0 산출물(Card/Slider/Toggle/타입/mock)을 재사용한다.

## 표시 요소 (UI_GUIDE ⑥ 기준)

| 요소 | 설명 | 데이터 소스 |
|------|------|-------------|
| 공격적↔보수적 슬라이더 | 투자성향(0~100), 양 끝 라벨(보수적/공격적), 변경 시 사이징 미리보기 갱신 | `RiskProfile.risk_appetite` |
| 사이징 미리보기 | 성향에 따른 예상 포지션 가중치 · 스탑로스 ATR 배수(표시용 계산) | `algorithms/sizing.py` 개념 반영 |
| 섹터 화이트/블랙리스트 | 선호/제외 섹터 입력(쉼표 구분) | `RiskProfile.sector_whitelist`, `.sector_blacklist` |
| 매매 시간대 | 시작~종료 시간 입력(`time`) | 로컬 기본값 |
| 알림 설정 | 슬랙 / SMS 토글 | 로컬 기본값 |

### 성향 슬라이더 & 사이징 미리보기
- 슬라이더는 step 0 `Slider` 프리미티브를 재사용한다(양 끝 라벨 leftLabel="보수적", rightLabel="공격적").
- 값(0~100)을 appetite(0.0~1.0)로 정규화: `appetite = value / 100`.
- 포지션 가중치(표시용) = `0.5 + 0.5 * appetite` — `sizing.risk_appetite_weight`와 동일 매핑. 범위 (0.5, 1.0].
- 스탑로스 ATR 배수(표시용) = `1.5 + 1.5 * appetite` — 공격적일수록 넓은 스탑(표시 전용).
- 미리보기는 표시 전용 계산이며 실제 사이징(backend 권위)에 적용하지 않는다.

### 섹터 / 시간대 / 알림
- 화이트/블랙리스트는 쉼표로 구분된 텍스트 입력. 기본값은 `mockRiskProfile`.
- 매매 시간대는 시작/종료 `time` 입력(기본 09:30 ~ 16:00).
- 알림은 슬랙/SMS 두 개의 `Toggle`(기본 슬랙 ON / SMS OFF).
- 모든 입력/토글은 로컬 상태로만 반영한다(저장은 backend — 후속 연동).

## 데이터 로딩 규칙
- 기본값은 `mockRiskProfile`(step 0). 슬라이더/입력/토글 인터랙션이 있으므로 Client Component로 구현한다.
- 값을 매매 파라미터에 적용하지 않는다(입력/미리보기까지만 — backend 권위).

## 타입
- `RiskProfile`: `{ risk_appetite, sector_whitelist, sector_blacklist }`.
- 시간대/알림은 이 step 범위에서 로컬 상태로만 관리한다(타입 확장은 backend 연동 시).

## 엣지케이스
- 슬라이더 양 끝(0/100) → 미리보기 가중치 0.50 / 1.00, 크래시 없음.
- 리스트 입력 비움 → 로컬 상태 갱신(검증/저장은 backend 책임, 이 step 범위 밖).
