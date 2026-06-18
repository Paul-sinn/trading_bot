# 코딩 에이전트 작업 프롬프트 — Kelly 사이징 버그 수정 + 콜드스타트 기준

> 아래 블록 전체를 코딩 에이전트에게 복붙하세요.
> **최상위 권위는 `docs/STRATEGY.md`(전략 헌장)다.** 충돌 시 헌장이 이긴다.
> 프로젝트 규칙(`CLAUDE.md`, `docs/ADR.md`)과 **SDD → TDD 순서를 강제**한다.

---

## 0. 먼저 읽어라

작업 전 **`docs/STRATEGY.md` §6(목표함수·MDD)·§7(사이징)**을 읽어라. 핵심:
- 목표 = **Sharpe 극대화**, **MDD 설계 12~15% / 하드 20%**.
- **MDD 상한이 사이징의 governor다** — 풀 켈리(50%+ 낙폭)는 금지, 분수 켈리로 강하게 축소.
- 켈리 입력(승률·손익비)은 **백테스트/거래기록에서 추정**(`tasks/backtest-engine-prompt.md` 산출과 연결).
- 거래기록 없는 콜드스타트는 켈리 신뢰 금지 → 보수적 고정 비율로 시작, 표본 누적 시 점진 전환.

대상: `algorithms/sizing.py`(순수 함수, ADR-002), `specs/sizing.md`, `tests/test_sizing.py`.
지킬 것: ADR-003 하드캡(최종 1회 손실액이 `equity×max_risk_pct` 초과 금지), 순수 함수, conventional commits.

---

## 문제 1 — "half-Kelly" 라벨이 틀렸다 (수학 버그)

현재 `kelly_fraction(win_rate, win_loss_ratio, cap=0.25)`는 `min(f, cap)`을 반환 — 이건 fractional
Kelly가 아니라 단순 상한 클램프다. 진짜 fractional Kelly는 **모든 베팅을 비례 축소**해야 하는데, 현재는
cap 미만(0.25 미만)이면 손도 안 댄다.

| full Kelly | 진짜 half-Kelly (×0.5) | 현재 min(f,0.25) |
|----------:|----------------------:|----------------:|
| 0.40 | 0.20 | 0.25 |
| 0.10 | 0.05 | **0.10 (버그)** |
| 0.04 | 0.02 | **0.04 (버그)** |

### 요구사항
- `fraction`(비례 축소, 기본 `0.5`)과 `cap`(절대 상한, 기본 `0.25`)을 **별개 파라미터로 분리**.
- 계산: `f_full = win_rate - (1-win_rate)/win_loss_ratio` → `f_used = clamp(fraction × max(0, f_full), 0, cap)`.
- 엣지케이스 유지: `win_loss_ratio<=0`→0, `f_full<=0`→0, 반환 항상 `[0, cap]`.
- docstring 정직하게: "fractional Kelly with a hard cap". "half-Kelly=cap" 같은 오기 제거.
- `position_size` 호출부 갱신. ADR-003 하드캡(max_qty 클램프) 유지(2중 안전).

### 테스트(최소)
- full 0.40, fraction 0.5, cap 0.25 → 0.20 / full 0.10 → 0.05(현 버그면 0.10) / full 0.04 → 0.02
- fraction 1.0이면 cap만 적용(기존 동작). f_used가 cap 절대 초과 안 함(속성 테스트).

---

## 문제 2 — 거래기록 0일 때 Kelly 입력 기준 (콜드스타트)

백테스트 엔진도 거래기록도 없는 상태에서 Kelly를 신뢰하면 과대 베팅→파산. 표본 크기에 따라
**고정 비율 → 경험적 Kelly로 점진 전환**(shrinkage)하는 순수 함수를 추가.

```python
def effective_kelly_fraction(
    win_rate: float, win_loss_ratio: float, sample_size: int, *,
    fraction: float = 0.5, cap: float = 0.25,
    prior_fraction: float = 0.0, shrinkage_k: int = 30,
) -> float: ...
```
- 수축: `w = sample_size / (sample_size + shrinkage_k)`.
- `f_eff = w × kelly_fraction(win_rate, win_loss_ratio, fraction, cap) + (1-w) × prior_fraction`.
- `sample_size=0` → `w=0` → 순수 `prior_fraction`. 기본 `prior_fraction=0.0` = "거래기록 전엔 켈리 미사용,
  호출부의 보수적 고정 비율 사이징에 맡김". 표본↑ → 경험적 켈리 비중↑(자동 램프업).

### 테스트(최소)
- sample_size=0 → prior_fraction / 매우 큼 → ≈kelly_fraction / 단조성(표본↑→켈리 쪽) / `[0,cap]` 불변 / ADR-003 회귀.

---

## 문제 3 — MDD governor & 레짐 연계 (헌장 §6·§7·§8 반영)

켈리 분수는 추상적으로 정하지 않는다 — **MDD 한도가 결정한다.**

- **`fraction`은 "백테스트 MDD가 설계 목표 ≤15%로 나오도록" 역으로 튜닝**되어야 한다(헌장 §6).
  → 이 값은 백테스트 엔진과 함께 캘리브레이션. 코드는 `fraction`을 파라미터로 노출하고, 기본값은 보수적으로.
- **레짐 사이징 배수**(헌장 §8): 최종 사이즈에 레짐 배수를 곱하는 순수 함수/훅을 둔다.
  ```
  A 정상강세 → 1.0 (풀)   B 불안강세 → 축소(예 0.5)   C/D → 0 (신규 진입 없음)
  ```
  단 이 배수는 **켈리 위에 곱하는 별도 레이어**로 분리(켈리 함수 자체는 순수 유지). 정확한 배수는 백테스트 튜닝.
- **불변식**: 어떤 경로(켈리·레짐 배수·appetite)로도 최종 risk_amount는 `equity × max_risk_pct`(ADR-003) 및
  포트폴리오 MDD 예산을 초과하지 못한다.

### 문서화 (specs/sizing.md + docstring + docs/ADR.md ADR 1건)
1. 콜드스타트에선 켈리 미신뢰 → 보수적 고정 비율 시작.
2. 켈리 입력(win_rate/win_loss_ratio/sample_size)의 출처 = **백테스트 엔진**(1순위) → 실거래 로그(2순위).
   현재 백테스트 미구현이면 콜드스타트(고정 비율) 경로만 활성임을 명기.
3. `fraction`은 MDD 목표로 캘리브레이션되는 값임을 명기. 레짐 배수 연계 지점.

---

## 완료 기준
- [ ] `specs/sizing.md` 갱신(문제 1·2·3), `tests/test_sizing.py` Red→Green
- [ ] `algorithms/sizing.py`: fractional+cap 분리, `effective_kelly_fraction`, 레짐 배수 훅
- [ ] `docs/ADR.md`에 콜드스타트·MDD governor 사이징 ADR
- [ ] `pytest tests/test_sizing.py` 통과, 회귀 없음. 순수 함수·ADR-003 유지. STRATEGY.md와 일치.

## 주의
- `.env`·네트워크 금지(순수 함수). 백테스트 엔진은 별도 작업(의존성으로 명시).
- 모호하면 STRATEGY.md 따르고, 없으면 spec에 가정으로 적고 진행.
