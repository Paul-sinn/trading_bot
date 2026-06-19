# SPEC: policy (헌법 enforcement — 순수 모델 + 평가자)

헌법(docs/STRATEGY.md, docs/UNIVERSE_TIERS.md, docs/overnight_constitution_and_universe_audit.md)을
**코드로 강제**하는 순수 레이어. 티어 유니버스·리스크 모드(B/C)·두 리스크 불변식을 typed 모델로 표현하고,
후보/주문을 평가하는 **부수효과 없는 순수 함수**를 제공한다.

관련 문서: `docs/STRATEGY.md §3·§6`, `docs/UNIVERSE_TIERS.md`, `docs/ADR.md ADR-012`,
`config/universe_tiers.json`, `config/risk_profiles.json`(데이터 소스 — 로더는 별도 step),
`algorithms/sizing.py`(SYSTEM_MAX_RISK_PCT 출처 체인), `agents/risk.py`(RiskLimits·check_risk_gate).

CRITICAL: **부수효과 없는 순수 함수.** 파일/네트워크/DB/전역상태/난수 금지. pydantic 미사용(algorithms 순수
유지 — plain frozen dataclass). 이 모듈은 JSON을 읽지 않는다(로더는 step 2). 입력만으로 출력 결정.

CRITICAL (fail-closed): 입력이 불명(NaN)·무효(음수)이거나 심볼이 유니버스에 없으면 **차단(veto)** 쪽으로
판정한다. 헌법은 의심스러우면 막는다.

CRITICAL: 이 step은 enforcement **판정 로직**만 만든다. 실주문/브로커/실행/전략 시그널/사이징 수치 변경 없음.

## 두 리스크 불변식 (둘 다 통과해야 함 — AND)

사용자 확정: per-trade 리스크와 account-loss는 **다른 개념**이며 **둘 다** 통과해야 한다. 하나라도 실패면 veto.

1. **per-trade 리스크 캡** (기존, ADR-003 불변):
   `per_trade_risk_pct <= SYSTEM_MAX_RISK_PCT` — `SYSTEM_MAX_RISK_PCT = 0.05` 고정. **올리지 않는다.**
   출처: `algorithms.goal_planner.SYSTEM_MAX_RISK_PCT`(단일 진실 재사용 — 재정의 금지).
2. **account-loss 캡** (신규):
   `account_loss_pct <= risk_mode.account_loss_cap` — B = 0.07 / C = 0.10.
   `account_loss_pct = position_weight × stop_loss_pct`.

⚠️ B/C의 account-loss 캡(7%/10%)은 5% 시스템 하드캡을 **우회하는 허가가 아니다.** 두 검사는 독립이며 둘 다 강제된다.

## 데이터 모델 (frozen dataclass)

```python
@dataclass(frozen=True)
class RiskMode:
    name: str                       # "B" | "C"
    account_loss_cap: float         # B 0.07 / C 0.10 (분수)
    allowed_tiers: tuple[str, ...]  # B: 0,1,2,3,4A,4B ; C: 0,1,2
    tier2_whitelist_only: bool      # C=True (Tier2는 화이트리스트만)
    tier2_whitelist: tuple[str, ...]
    default: bool

@dataclass(frozen=True)
class TierEntry:
    symbol: str
    primary_tier: str               # "0".."6","4A","4B"
    tiers: tuple[str, ...]
    status: str                     # approved|watch|needs_review|reject|data_missing
    tradable: bool
    leveraged: bool

@dataclass(frozen=True)
class UniversePolicy:
    entries: tuple[TierEntry, ...]  # by_symbol 조회는 내부 dict로

@dataclass(frozen=True)
class RiskCheck:                    # evaluate_risk 결과 — 두 불변식 값/캡/판정/사유 모두 노출
    per_trade_risk_pct: float
    system_max_risk_pct: float
    per_trade_pass: bool
    account_loss_pct: float
    account_loss_cap: float
    account_loss_pass: bool
    passed: bool                    # per_trade_pass AND account_loss_pass
    reason: str                     # 사람이 읽는 사유(네 값 모두 포함)
```

## 함수 (순수)

### `account_loss_pct(position_weight, stop_loss_pct) -> float`
- `= position_weight × stop_loss_pct` (분수). 이 포지션이 계좌에 줄 수 있는 손실 비율.

### `evaluate_risk(per_trade_risk_pct, position_weight, stop_loss_pct, mode) -> RiskCheck`
- check1 `per_trade_pass = per_trade_risk_pct <= SYSTEM_MAX_RISK_PCT`.
- check2 `account_loss_pass = account_loss_pct(weight, stop) <= mode.account_loss_cap`.
- `passed = per_trade_pass AND account_loss_pass`. 경계(== 캡)는 통과(`>`만 위반 — RiskAgent와 동일).
- **fail-closed**: 입력 중 NaN 또는 음수가 있으면 `passed=False`, 사유 명시(무효 입력 → 차단).
- `reason`은 per_trade_risk_pct, SYSTEM_MAX_RISK_PCT, account_loss_pct, account_loss_cap, 어느 검사가
  깨졌는지를 모두 담는다.

### `tier_status(symbol, universe) -> str | None`
- 심볼의 status. 미등록 → None.

### `is_candidate_eligible(symbol, universe) -> bool`
- 후보 적격(헌법 dry-run 템플릿): `등록됨 AND tradable AND status ∉ {reject, data_missing}`.
- 미등록 심볼 → False(fail-closed). Tier0 컴퍼스(tradable=False) → False.
- ⚠️ `needs_review`는 이 **coarse 적격 필터는 통과**한다(템플릿: reject·data_missing만 제외). 데이터
  부족·이상치 차단은 별도 hard-veto 레이어(후속 step)에서 fine하게 처리한다.

### `mode_allows_symbol(symbol, mode, universe) -> bool`
- `등록됨 AND primary_tier ∈ mode.allowed_tiers`.
- `mode.tier2_whitelist_only AND primary_tier == "2"`면 `symbol ∈ mode.tier2_whitelist`여야 True.
- 미등록 → False(fail-closed).

## 엣지케이스
- per_trade/weight/stop 중 음수·NaN → evaluate_risk veto(fail-closed).
- account_loss == cap, per_trade == SYSTEM_MAX → 통과(경계 허용).
- 미등록 심볼 → tier_status None, is_candidate_eligible False, mode_allows_symbol False.
- C 모드 + Tier2 비화이트리스트(예: SMCI) → mode_allows_symbol False.
- C 모드 + Tier3+ → allowed_tiers 밖 → False.

## 비범위 (이 step에서 하지 않음 — 후속 step)
- JSON 로더(`config/*.json` → 모델). step 2.
- Concentration Phase 캡 → position_weight 제안 로직. (evaluate_risk는 weight를 입력으로만 받는다.)
- hard-veto 12조건 종합(`agents/risk.py` 확장). step 4.
- dry-run 리포트 객체/빌더. step 5.
- 실주문/브로커/실행/전략 시그널/사이징 수치. (불변)
