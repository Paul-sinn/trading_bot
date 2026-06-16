# Step 6: profile-settings-page (⑥ 투자성향 설정)

## 읽어야 할 파일

- `/CLAUDE.md`, `/docs/UI_GUIDE.md` (⑥ 투자성향 설정, 슬라이더 규칙: 공격적↔보수적 양 끝 라벨)
- `/docs/PRD.md` (핵심기능 6: 투자성향 반영, 슬라이더 변경 시 포지션 크기·스탑로스 재계산)
- `/frontend/src/components/ui/` (Slider/입력 — step 0)
- `/frontend/src/lib/{api,mock}.ts`, `/frontend/src/types/index.ts` (step 0)
- `/algorithms/sizing.py` (risk_appetite_weight / position_size — 성향이 사이징에 미치는 영향 참고)

## 작업

투자성향 설정 페이지(`frontend/src/app/profile/page.tsx`). **SDD → TDD**: `specs/profile_settings_page.md` → 스모크 테스트(Red) → 구현.

요소 (UI_GUIDE ⑥):
- **공격적 ↔ 보수적 슬라이더**: Slider 프리미티브, 양 끝 라벨 명시. 값(0~1) 변경 시 예상 포지션 크기·스탑로스 배수 미리보기를 갱신(클라이언트에서 `sizing` 개념 반영한 표시용 계산; 실제 적용은 backend).
- **섹터 화이트리스트 / 블랙리스트**: 섹터 토글/입력.
- **매매 시간대 설정**: 시작~종료 시간 입력.
- **알림 설정**: 슬랙 / SMS 토글.
- Client Component(슬라이더/토글 인터랙션). mock 기본값. 저장은 backend 후속.

## Acceptance Criteria

```bash
cd frontend && npm run build && npm run lint && npm test
```

테스트: `frontend/src/__tests__/profile.test.tsx` — 성향 슬라이더(양 끝 라벨) 렌더, 슬라이더 변경 시 미리보기 값 갱신, 화이트/블랙리스트·시간대·알림 토글 존재.

## 검증 절차

1. AC 실행. 그리고 **전체 빌드 최종 확인**: 6개 페이지 라우트가 모두 빌드되는지(`npm run build` 출력에 `/`, `/daily`, `/weekly`, `/direction`, `/goals`, `/profile`).
2. 체크리스트: 슬라이더 양 끝 라벨? 변경 시 사이징 미리보기 갱신? 4개 설정군(성향/섹터/시간대/알림) 포함? AI 슬롭 미사용?
3. `phases/2-frontend/index.json`의 step 6 업데이트.

## 금지사항

- 슬라이더 변경을 실제 매매 파라미터에 직접 적용하지 마라. 이유: backend 권위. UI는 미리보기/입력까지.
- 새 프리미티브 만들지 마라. 다른 페이지를 건드리지 마라. 기존 테스트(Python 189 + frontend 누적)를 깨뜨리지 마라.
