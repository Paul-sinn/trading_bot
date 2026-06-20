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

## hard-veto 종합 (step 4 — 헌법 RiskGate 12조건)

후보 1건의 모든 사실을 받아 헌법 hard-veto 조건을 **한 번에 평가**하는 순수 함수. config의
`riskgate_hard_veto_conditions`(docs/UNIVERSE_TIERS §6) + 두 리스크 불변식 + 티어 적격 + needs_review를
종합한다. fail-closed: 입력이 비거나 False면 막는 쪽.

⚠️ 배치: `agents/risk.py`가 아니라 **여기(algorithms/policy.py)**에 둔다 — `agents.risk`를 import하면
`policy → goal_planner → agents.risk` 순환이 된다. 이 함수는 순수 per-candidate 평가자이며, 전역 게이트
`agents.risk.check_risk_gate`(단일 진입점)를 **대체하지 않는다**. 라이브 배선(주문 전 경로 삽입)은 후속 step.

```python
@dataclass(frozen=True)
class VetoInput:
    symbol: str
    mode: RiskMode
    universe: UniversePolicy
    per_trade_risk_pct: float        # sizing 브리지 산출(분수)
    position_weight: float
    stop_loss_pct: float
    regime: Regime | None = None     # None → fail-closed veto
    has_stop_loss: bool = False      # 모든 bool 기본 False = fail-closed(누락 시 막힘)
    position_size_ok: bool = False
    liquidity_ok: bool = False
    tier_exposure_ok: bool = False
    data_ok: bool = False
    ipo_data_ok: bool = False
    event_risk_checked: bool = False
    technical_confirmation: bool = False
    manual_override: bool = False    # needs_review 수동 승인

@dataclass(frozen=True)
class VetoResult:
    passed: bool
    reasons: tuple[str, ...]         # veto 사유 전부(통과면 빈 튜플)
    risk_check: RiskCheck            # 두 불변식 상세
```

### `evaluate_hard_veto(inp) -> VetoResult`
모든 사유를 모아서(첫 위반에서 멈추지 않음 — 사람 검토용) 반환. veto 조건:
1. `is_candidate_eligible` 실패(reject/data_missing/비거래/미등록)
2. `tier_status == needs_review` AND `manual_override` 아님 → veto (concern: SMCI 류 자동통과 금지)
3. `mode_allows_symbol` 실패(C가 Tier3+ 또는 Tier2 비화이트리스트 등)
4. `evaluate_risk(...)` 두 불변식 실패(per-trade 5% / account-loss 모드캡, AND)
5. `has_stop_loss` False / 6. `position_size_ok` False / 7. `liquidity_ok` False
8. regime risk-off 또는 None(`policy_for(regime).allow_new_entry` False) — algorithms.regime 재사용
9. `tier_exposure_ok` False / 10. `data_ok` False / 11. `ipo_data_ok` False
12. `event_risk_checked` False / 13. `technical_confirmation` False(AI/news 단독 금지)
`passed = (사유 0개)`.

### 포트폴리오 손실한도(daily/weekly/consecutive)는 비범위
헌법 12조건 중 daily/weekly/consecutive loss limit은 **계좌-상태 가드**로 RiskAgent(agents/risk.py
`evaluate`, 이미 MDD/손실/포지션 구현) 도메인이다. per-candidate veto가 아니며, 현재 config에서
`data_missing`(미확정 TODO)이라 비활성. 미설정 정책 노브를 per-candidate에서 막아 모든 거래를 영구
veto하지 않는다(설정되면 RiskAgent 레이어에서 강제).

## Concentration Phase → position_weight 제안 (정책 기반)

계좌 phase·tier·리스크 모드·집중 규칙·소액전용 규칙으로 **position_weight를 제안**한다. ⚠️ 제안일 뿐
거래 허가가 아니다 — 제안 weight도 이후 hard-veto/evaluate_risk(account_loss = weight × stop ≤ 모드캡)를
통과해야 한다. 위반하면 축소 제안(needs_adjustment)하거나 거부(rejected).

데이터: `config/risk_profiles.json.concentration_phases`. **Phase 1만 per-tier 캡(deploy_caps_pct) 정의**,
Phase 2~3은 단일포지션 캡(main_pct), Phase 4는 portfolio(per-position 캡 미정의). 발명 금지 — config에
없는 수치는 needs_adjustment로 표시.

```python
@dataclass(frozen=True)
class TierWeightCap:           # 한 티어밴드 배치 캡(분수). special 있으면 range 무시
    low: float | None; high: float | None; special: str | None  # "small_only"|"conservative"|None
@dataclass(frozen=True)
class ConcentrationPhase:
    phase: str; account_usd_min: float; account_usd_max: float | None
    mode: str | None; tier_caps: dict[str, TierWeightCap]; single_position_low: float | None
@dataclass(frozen=True)
class ConcentrationPolicy:
    phases: tuple[ConcentrationPhase, ...]
    def phase(name) -> ConcentrationPhase | None
    def phase_for_equity(equity) -> ConcentrationPhase | None
@dataclass(frozen=True)
class WeightSuggestion:
    status: str                # ok | needs_adjustment | rejected | small_only
    suggested_weight: float | None
    raw_weight: float | None
    account_loss_at_suggested: float | None
    account_loss_cap: float
    reason: str
```

### `suggest_position_weight(phase, primary_tier, mode, stop_loss_pct, concentration) -> WeightSuggestion`
순서(fail-closed):
1. `primary_tier ∉ mode.allowed_tiers` → **rejected**(C-mode 제한 등 — 정책 허용 티어만).
2. `primary_tier == "5"` → **small_only**(모든 phase에서 concentration 금지, suggested None — 수동 소액).
   tier5 exposure cap은 config에서 data_missing.
3. phase 미정의 → rejected.
4. 티어밴드 캡: Phase1은 `tier_caps[band].low`(보수적 하단). special `small_only`→small_only,
   `conservative`(Tier6)→needs_adjustment(수동). per-tier 없고 `single_position_low` 있으면(Phase2~3)
   그 값. 둘 다 없으면(Phase4)→needs_adjustment(포트폴리오 수동).
5. `stop_loss_pct <= 0`/NaN → rejected.
6. `account_loss = raw × stop`: `≤ cap`이면 **ok**(suggested=raw). `> cap`이면 **needs_adjustment**
   (suggested = `cap / stop` 로 축소 — account_loss 정확히 캡에 맞춤).

티어밴드 매핑: 0/1/2→tier_0_2, 3→tier_3, 4A→tier_4A, **4B→tier_4B(더 낮은 캡)**, 5→tier_5, 6→tier_6.
C-mode Tier2 화이트리스트(심볼레벨)는 여기서 안 본다 — hard-veto(mode_allows_symbol)가 담당(중복 금지).

## 비범위 (이 step에서 하지 않음 — 후속 step)
- JSON 로더(`config/*.json` → 모델). ✅ step 2(+ 이번에 concentration 파싱 추가).
- sizing→불변식 분수 브리지. ✅ step 3.
- dry-run 리포트 객체/빌더. ✅ step 5.
- 체결/슬리피지 시뮬, Phase4 포트폴리오 per-position 사이징(수치 일부 data_missing).
- hard-veto를 주문 전 경로/`check_risk_gate`에 라이브 배선. (실행 범위 밖)
- 실주문/브로커/실행/전략 시그널/사이징 수치. (불변)
