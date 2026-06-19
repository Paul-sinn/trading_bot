# Dry-Run Decision Report — TEMPLATE (no execution)

> ⚠️ **TEMPLATE / DRY-RUN ONLY.** 이 문서는 *형식 템플릿*이다. 실제 주문·실행·라이브 진입과 무관하며,
> 이 템플릿을 채우는 자동 코드도 아직 없다. 값은 모두 플레이스홀더(`<...>`)다.
>
> 목적: 야간 분석(헌장 §5)이 산출할 "후보별 판단 리포트"가 어떤 형태여야 하는지 고정 — 알고리즘 3레이어 →
> LLM 판단 → RiskGate 순서(헌장 CRITICAL)와 정책(`config/risk_profiles.json`)을 사람이 검토 가능한 표로.
> **이건 universe audit/정책 단계의 산출물 형식이지 live greenlight가 아니다.**

## 헤더
```
report_date      : <YYYY-MM-DD>           # 마감 후 생성
account_phase    : <1|2|3|4>              # config/risk_profiles.json concentration_phases
risk_mode        : <B|C>                  # 기본 B
regime           : <A|B|C|D>              # SPY 200d + VIX (헌장 §8)
compass_state    : <strong|mixed|weak>   # QQQ/SMH/XLK + 메가캡 (UNIVERSE_TIERS §1)
mode             : DRY-RUN                # 항상 DRY-RUN. 주문 미발생.
```

## 후보별 판단 (per-candidate)
| 필드 | 값(예시 플레이스홀더) |
|---|---|
| symbol | `<TICKER>` |
| tier / status | `<tier>` / `<approved\|watch\|needs_review>` (reject·data_missing → 후보 제외) |
| trend_score | `<pass\|fail>` |
| volume_score | `<pass\|fail>` |
| relative_strength vs QQQ/SMH | `<pass\|fail>` |
| regime gate (A/B만) | `<pass\|fail>` |
| liquidity / spread | `<pass\|fail>` (ADV `<$M>`) |
| stop_loss defined | `<yes\|no>` (`<ATR/구조 기반>`) |
| risk_reward | `<R:R>` (≥ 1:2 선호) |
| data ok (결측/이상치 없음) | `<yes\|no>` |
| news/AI evidence | `<boost\|warning\|none>` (※ 단독 매수 불가) |
| — RiskGate hard veto — | `<PASS\|VETO: 사유>` (config/risk_profiles.json) |
| position_weight (제안) | `<%>` (Concentration Phase 캡 이내) |
| account_loss = weight × stop_pct | `<%>` (모드 한도 B −7% / C −10% 이내) |
| **decision (DRY-RUN)** | `<BUY\|HOLD\|SELL>` |
| rationale | `<짧은 근거 — LLM 설명, 숫자 미변경>` |

## 푸터 / 불변식 체크
```
orders_placed        : 0            # 항상 0 (DRY-RUN)
riskgate_vetoes      : <n>          # veto된 후보 수 + 사유
mdd_hard_stop        : 0.20 (불변)
no_return_guarantee  : true
notes                : 자동 라이브 진입 없음. 사람 검토용 리포트.
```

## 사용 메모
- 이 템플릿은 **형식 정의**일 뿐, 채우는 로직(스캐너·LLM·RiskGate 배선)은 별도 검증 단계에서 구현.
- `reject`·`data_missing` 티커는 후보 목록에서 제외하고 사유를 남긴다.
- 검증 게이트(헌장 §10: 약세장 OOS 등)와 무관하게 이 리포트만으로 라이브 진입하지 않는다.
