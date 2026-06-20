"""헌법 enforcement — 순수 모델 + 평가자 (부수효과 없는 순수 함수).

헌법(docs/STRATEGY.md §3·§6, docs/UNIVERSE_TIERS.md, docs/ADR.md ADR-012)을 코드로 강제하는
순수 레이어. 티어 유니버스·리스크 모드(B/C)·두 리스크 불변식을 typed frozen dataclass로 표현하고,
후보/주문을 평가하는 순수 함수를 제공한다.

ADR-002: 파일/네트워크/DB/전역상태/난수 금지. pydantic 미사용(plain dataclass). 이 모듈은 JSON을 읽지
않는다 — `config/*.json` → 모델 로더는 별도 step(여기는 모델·로직만). 입력만으로 출력 결정.

fail-closed(헌법): 입력이 NaN/음수이거나 심볼이 유니버스에 없으면 차단(veto) 쪽으로 판정한다.

두 리스크 불변식 (사용자 확정 — 둘은 다른 개념, **둘 다** 통과해야 한다):
  1. per-trade 리스크 캡(ADR-003 불변): per_trade_risk_pct <= SYSTEM_MAX_RISK_PCT(=0.05, 올리지 않음).
     SYSTEM_MAX_RISK_PCT는 algorithms.goal_planner의 단일 진실을 재사용한다.
  2. account-loss 캡(신규): account_loss_pct(= weight × stop) <= risk_mode.account_loss_cap(B 0.07/C 0.10).
  ⚠️ B/C의 7%/10%는 5% 시스템 하드캡을 우회하는 허가가 아니다. 두 검사는 독립이며 둘 다 강제된다.

spec: specs/policy.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# 단일 진실 재사용(재정의 금지) — ADR-003 시스템 하드캡. 헌법 per-trade 검사가 이 값을 강제한다.
from algorithms.goal_planner import SYSTEM_MAX_RISK_PCT
from algorithms.regime import Regime, policy_for

# 후보 적격에서 제외하는 status (헌법 dry-run 템플릿: reject·data_missing만 제외).
ELIGIBLE_EXCLUDED_STATUSES = frozenset({"reject", "data_missing"})
VALID_STATUSES = frozenset(
    {"approved", "watch", "needs_review", "reject", "data_missing"}
)


# --- 데이터 모델 (frozen) ---


@dataclass(frozen=True)
class RiskMode:
    """리스크 모드(B 기본 / C 예외적). 수치는 로더가 config/risk_profiles.json에서 채운다."""

    name: str
    account_loss_cap: float          # B 0.07 / C 0.10 (분수)
    allowed_tiers: tuple[str, ...]   # B: 0,1,2,3,4A,4B ; C: 0,1,2
    tier2_whitelist_only: bool       # C=True (Tier2는 화이트리스트만)
    tier2_whitelist: tuple[str, ...]
    default: bool


@dataclass(frozen=True)
class TierEntry:
    """티어 유니버스의 한 종목(감사 산출). config/universe_tiers.json 의 한 row."""

    symbol: str
    primary_tier: str                # "0".."6","4A","4B"
    tiers: tuple[str, ...]
    status: str                      # approved|watch|needs_review|reject|data_missing
    tradable: bool
    leveraged: bool


@dataclass(frozen=True)
class UniversePolicy:
    """티어 유니버스 전체. 심볼 조회는 내부 dict로 O(1)."""

    entries: tuple[TierEntry, ...]
    _by_symbol: dict[str, TierEntry] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        # frozen이라 직접 대입 불가 → object.__setattr__로 캐시 구성(순수성 유지: 입력만으로 결정).
        object.__setattr__(
            self, "_by_symbol", {e.symbol: e for e in self.entries}
        )

    def get(self, symbol: str) -> TierEntry | None:
        return self._by_symbol.get(symbol)


@dataclass(frozen=True)
class RiskCheck:
    """두 리스크 불변식의 평가 결과. 값/캡/판정/사유를 모두 노출한다(사람 검토용)."""

    per_trade_risk_pct: float
    system_max_risk_pct: float
    per_trade_pass: bool
    account_loss_pct: float
    account_loss_cap: float
    account_loss_pass: bool
    passed: bool
    reason: str


@dataclass(frozen=True)
class PortfolioGuards:
    """포트폴리오 가드(헌법 §6). 미정 한도는 None(헌법 data_missing — 후속 hard-veto가 차단으로 해석)."""

    mdd_hard_stop_pct: float                  # 불변 0.20
    daily_loss_limit_pct: float | None        # "data_missing" → None
    weekly_loss_limit_pct: float | None
    consecutive_loss_limit_count: int | None


@dataclass(frozen=True)
class Policy:
    """집계 정책 — 로더가 config/*.json 에서 빌드한다. 순수 컨테이너(I/O 없음)."""

    universe: UniversePolicy
    risk_modes: tuple[RiskMode, ...]
    portfolio_guards: PortfolioGuards

    def mode(self, name: str) -> RiskMode | None:
        """이름으로 리스크 모드 조회. 없으면 None."""
        return next((m for m in self.risk_modes if m.name == name), None)

    @property
    def default_mode(self) -> RiskMode | None:
        """default=True 모드(헌법 기본 B). 없으면 None."""
        return next((m for m in self.risk_modes if m.default), None)


# --- 순수 평가 함수 ---


def account_loss_pct(position_weight: float, stop_loss_pct: float) -> float:
    """이 포지션이 계좌에 줄 수 있는 손실 비율 = position_weight × stop_loss_pct (분수)."""
    return position_weight * stop_loss_pct


def _is_bad(*values: float) -> bool:
    """fail-closed 입력 검사: NaN 또는 음수면 True(무효)."""
    return any(math.isnan(v) or v < 0 for v in values)


def evaluate_risk(
    per_trade_risk_pct: float,
    position_weight: float,
    stop_loss_pct: float,
    mode: RiskMode,
) -> RiskCheck:
    """두 리스크 불변식을 독립 평가한다. 둘 다 통과해야 passed=True (하나라도 실패면 veto).

    check1 per_trade_risk_pct <= SYSTEM_MAX_RISK_PCT(0.05, 불변).
    check2 account_loss_pct(weight, stop) <= mode.account_loss_cap(B 0.07/C 0.10).
    경계(== 캡)는 통과(`>`만 위반 — RiskAgent와 동일). 입력 NaN/음수면 fail-closed veto.
    reason은 네 값(per_trade, system_max, account_loss, cap)을 모두 담는다.
    """
    al = account_loss_pct(position_weight, stop_loss_pct)

    if _is_bad(per_trade_risk_pct, position_weight, stop_loss_pct):
        return RiskCheck(
            per_trade_risk_pct=per_trade_risk_pct,
            system_max_risk_pct=SYSTEM_MAX_RISK_PCT,
            per_trade_pass=False,
            account_loss_pct=al,
            account_loss_cap=mode.account_loss_cap,
            account_loss_pass=False,
            passed=False,
            reason=(
                f"무효 입력(NaN/음수) → fail-closed veto: "
                f"per_trade={per_trade_risk_pct}, weight={position_weight}, stop={stop_loss_pct}"
            ),
        )

    per_trade_pass = per_trade_risk_pct <= SYSTEM_MAX_RISK_PCT
    account_loss_pass = al <= mode.account_loss_cap
    passed = per_trade_pass and account_loss_pass

    pt = (
        f"per_trade {per_trade_risk_pct:.4f} {'<=' if per_trade_pass else '>'} "
        f"SYSTEM_MAX {SYSTEM_MAX_RISK_PCT:.4f}"
    )
    ac = (
        f"account_loss {al:.4f} {'<=' if account_loss_pass else '>'} "
        f"{mode.name} cap {mode.account_loss_cap:.4f}"
    )
    verdict = "PASS" if passed else "VETO"
    reason = f"{verdict}: [{pt}] AND [{ac}]"

    return RiskCheck(
        per_trade_risk_pct=per_trade_risk_pct,
        system_max_risk_pct=SYSTEM_MAX_RISK_PCT,
        per_trade_pass=per_trade_pass,
        account_loss_pct=al,
        account_loss_cap=mode.account_loss_cap,
        account_loss_pass=account_loss_pass,
        passed=passed,
        reason=reason,
    )


def tier_status(symbol: str, universe: UniversePolicy) -> str | None:
    """심볼의 status. 미등록이면 None."""
    entry = universe.get(symbol)
    return entry.status if entry is not None else None


def is_candidate_eligible(symbol: str, universe: UniversePolicy) -> bool:
    """후보 적격(헌법 dry-run 템플릿 coarse 필터).

    등록됨 AND tradable AND status ∉ {reject, data_missing}. 미등록·컴퍼스(tradable=False) → False.
    needs_review는 이 단계 통과(데이터부족 차단은 후속 hard-veto 레이어 — spec 비범위).
    """
    entry = universe.get(symbol)
    if entry is None or not entry.tradable:
        return False
    return entry.status not in ELIGIBLE_EXCLUDED_STATUSES


def mode_allows_symbol(symbol: str, mode: RiskMode, universe: UniversePolicy) -> bool:
    """모드가 이 심볼의 티어를 허용하는지. C는 Tier0~2 + Tier2 화이트리스트로 제한.

    등록됨 AND primary_tier ∈ mode.allowed_tiers. tier2_whitelist_only이고 Tier2면 화이트리스트만.
    미등록 → False(fail-closed).
    """
    entry = universe.get(symbol)
    if entry is None:
        return False
    if entry.primary_tier not in mode.allowed_tiers:
        return False
    if mode.tier2_whitelist_only and entry.primary_tier == "2":
        return entry.symbol in mode.tier2_whitelist
    return True


# --- hard-veto 종합 (헌법 RiskGate per-candidate 평가자) ---
# ⚠️ agents.risk가 아니라 여기 둔다(policy→goal_planner→agents.risk 순환 회피). 이 함수는 순수
# per-candidate 평가자이며 전역 게이트 agents.risk.check_risk_gate(단일 진입점)를 대체하지 않는다.


@dataclass(frozen=True)
class VetoInput:
    """후보 1건의 모든 사실. bool 기본 False·regime None = fail-closed(누락 시 막힘)."""

    symbol: str
    mode: RiskMode
    universe: UniversePolicy
    per_trade_risk_pct: float        # sizing 브리지 산출(분수)
    position_weight: float
    stop_loss_pct: float
    regime: Regime | None = None
    has_stop_loss: bool = False
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
    """hard-veto 결과. reasons는 모든 veto 사유(통과면 빈 튜플). risk_check는 두 불변식 상세."""

    passed: bool
    reasons: tuple[str, ...]
    risk_check: RiskCheck


def evaluate_hard_veto(inp: VetoInput) -> VetoResult:
    """헌법 RiskGate hard-veto를 한 번에 평가한다(모든 사유 수집 — 첫 위반에서 멈추지 않음).

    적격·needs_review·모드허용·두 리스크 불변식·per-candidate 게이트·레짐을 종합한다. fail-closed:
    누락/False/불명은 막는 쪽. passed = (사유 0개). 포트폴리오 손실한도(daily/weekly/consecutive)는
    계좌-상태 가드(RiskAgent 도메인)라 여기 미포함(spec 참조).
    """
    sym, mode, u = inp.symbol, inp.mode, inp.universe
    reasons: list[str] = []

    if not is_candidate_eligible(sym, u):
        reasons.append(f"{sym}: 후보 적격 아님(reject/data_missing/비거래/미등록)")
    if tier_status(sym, u) == "needs_review" and not inp.manual_override:
        reasons.append(f"{sym}: needs_review — manual override 없음")
    if not mode_allows_symbol(sym, mode, u):
        reasons.append(f"{sym}: 모드 {mode.name}가 티어 불허(C-mode 제한 등)")

    risk_check = evaluate_risk(
        inp.per_trade_risk_pct, inp.position_weight, inp.stop_loss_pct, mode
    )
    if not risk_check.passed:
        reasons.append(f"리스크 불변식 veto: {risk_check.reason}")

    if not inp.has_stop_loss:
        reasons.append("stop_loss 없음")
    if not inp.position_size_ok:
        reasons.append("position_size 계산 실패/0")
    if not inp.liquidity_ok:
        reasons.append("liquidity/spread 기준 실패")
    if inp.regime is None or not policy_for(inp.regime).allow_new_entry:
        reasons.append(f"market regime risk-off/불명({inp.regime})")
    if not inp.tier_exposure_ok:
        reasons.append("sector/tier exposure 과다")
    if not inp.data_ok:
        reasons.append("데이터 결측/이상치")
    if not inp.ipo_data_ok:
        reasons.append("IPO/신규상장 데이터 부족 + 특례 없음")
    if not inp.event_risk_checked:
        reasons.append("earnings/FOMC/CPI 등 고임팩트 이벤트 리스크 미확인")
    if not inp.technical_confirmation:
        reasons.append("AI/news/SNS만 있고 technical confirmation 없음")

    return VetoResult(passed=len(reasons) == 0, reasons=tuple(reasons), risk_check=risk_check)
