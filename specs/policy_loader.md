# SPEC: policy_loader (config/*.json → 순수 Policy 모델)

`config/universe_tiers.json` + `config/risk_profiles.json`(선언적 정책 데이터, SSOT)을 읽어 검증하고
`algorithms.policy.Policy`(순수 frozen 모델)로 빌드한다. **I/O(파일 읽기)이므로 agents/에 둔다**(ADR-001).

관련: `specs/policy.md`(순수 모델), `config/README.md`, `config/risk_profiles.json`,
`config/universe_tiers.json`, `tests/test_config_universe.py`(JSON 형식 검증 — 중복 안 함).

CRITICAL (fail-closed): 파일 없음·JSON 깨짐·필수필드 누락·타입 불일치·status enum 위반·default 모드
부재/중복이면 부분 Policy를 만들지 않고 `PolicyConfigError`로 **즉시 차단**한다. 의심스러우면 막는다.

CRITICAL: 이 모듈은 정책을 **데이터로 로드**할 뿐, 전략 시그널/사이징 수치/주문/브로커/실거래를 건드리지
않는다. 값 튜닝 없음 — JSON에 있는 값을 그대로 typed 모델로 옮긴다(SSOT는 JSON).

## 집계 모델 (algorithms/policy.py 에 추가 — 순수)

```python
@dataclass(frozen=True)
class PortfolioGuards:
    mdd_hard_stop_pct: float                  # 불변 0.20
    daily_loss_limit_pct: float | None        # "data_missing" → None
    weekly_loss_limit_pct: float | None
    consecutive_loss_limit_count: int | None

@dataclass(frozen=True)
class Policy:
    universe: UniversePolicy
    risk_modes: tuple[RiskMode, ...]
    portfolio_guards: PortfolioGuards
    def mode(self, name: str) -> RiskMode | None
    @property
    def default_mode(self) -> RiskMode | None
```

## 함수

### `load_policy(config_dir=None) -> Policy`
- `config_dir=None`이면 레포 `config/`(이 파일 기준 `parents[1]/config`).
- `universe_tiers.json` → `UniversePolicy(entries=...)`. 각 ticker → `TierEntry`.
- `risk_profiles.json.risk_modes` → 각 `RiskMode`. `c_mode_tier2_whitelist`(top-level)은
  `tier2_whitelist_only=True`인 모드에만 부착(아니면 `()`).
- `risk_profiles.json.portfolio_guards` → `PortfolioGuards`.
- 파일은 `encoding="utf-8"`로 읽는다(윈도우 cp949 회피).

## 필드 매핑
- `RiskMode.account_loss_cap` ← `mode["max_account_loss_pct_per_trade"]`
- `RiskMode.allowed_tiers` ← `tuple(mode["allowed_tiers"])`
- `RiskMode.tier2_whitelist_only` ← `mode.get("tier2_whitelist_only", False)`
- `RiskMode.tier2_whitelist` ← whitelist_only면 `tuple(root["c_mode_tier2_whitelist"])`, 아니면 `()`
- `RiskMode.default` ← `mode["default"]`; `RiskMode.name` ← 모드 키("B"/"C")
- `TierEntry` ← ticker의 symbol/primary_tier/tiers/status/tradable/leveraged
- `PortfolioGuards`: `mdd_hard_stop_pct` 필수 number; daily/weekly/consecutive는 number 또는
  `"data_missing"`(→ None). 그 외 문자열 → 에러.

## 검증 (위반 시 PolicyConfigError)
- universe: `tickers` 비어있지 않은 list. 각 ticker: symbol(str), primary_tier(str),
  tiers(비어있지 않은 list[str]), status ∈ {approved,watch,needs_review,reject,data_missing},
  tradable(bool), leveraged(bool).
- risk_modes: dict, ≥1 모드. 각 모드: max_account_loss_pct_per_trade(number ≥ 0),
  allowed_tiers(list[str]), default(bool). **정확히 하나의 default=True**.
- portfolio_guards.mdd_hard_stop_pct: number.
- 파일 없음/JSON 파싱 실패 → PolicyConfigError(원인 메시지 포함).

## 엣지케이스
- `data_missing` 손실한도 → None(통과 아님 — 후속 hard-veto가 None을 차단으로 해석).
- `_meta` 등 추가 키는 무시(extra ignore).
- 알 수 없는 status 문자열 → 에러(fail-closed).

## 비범위 (이 step에서 하지 않음)
- Concentration Phase 캡 파싱(후속 — position_weight 제안 로직과 함께).
- hard-veto 종합·dry-run 리포트·실주문/브로커/전략 로직.
- C 화이트리스트 ⊆ Tier2 같은 교차검증(그건 tests/test_config_universe.py 담당).
