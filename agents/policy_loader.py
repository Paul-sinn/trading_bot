"""정책 config 로더 — config/*.json → 순수 algorithms.policy.Policy 모델.

선언적 정책 데이터(SSOT: config/universe_tiers.json + config/risk_profiles.json)를 읽고 검증해
typed 순수 모델로 빌드한다. 파일 읽기 I/O라 agents/에 둔다(ADR-001). 값 튜닝 없음 — JSON 값을
그대로 옮긴다(SSOT는 JSON).

CRITICAL (fail-closed): 파일 없음·JSON 깨짐·필수필드 누락·타입 불일치·status enum 위반·default 모드
부재/중복이면 부분 Policy를 만들지 않고 PolicyConfigError로 즉시 차단한다.

CRITICAL: 정책을 데이터로 로드만 한다. 전략 시그널/사이징 수치/주문/브로커/실거래를 건드리지 않는다.

spec: specs/policy_loader.md
"""

from __future__ import annotations

import json
from numbers import Real
from pathlib import Path

from algorithms.policy import (
    VALID_STATUSES,
    Policy,
    PortfolioGuards,
    RiskMode,
    TierEntry,
    UniversePolicy,
)

# 레포 기본 config/ (이 파일 기준 상위 = 레포 루트).
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

# 손실 한도가 "미정"임을 뜻하는 센티넬(헌법) → 모델에선 None.
_DATA_MISSING = "data_missing"


class PolicyConfigError(Exception):
    """정책 config가 없거나 깨졌거나 검증에 실패했을 때(fail-closed)."""


# --- 작은 검증 헬퍼 ---


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PolicyConfigError(msg)


def _read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PolicyConfigError(f"config 파일 없음: {path}") from exc
    except OSError as exc:  # 권한/디렉토리 등
        raise PolicyConfigError(f"config 파일 읽기 실패: {path} ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PolicyConfigError(f"JSON 파싱 실패: {path} ({exc})") from exc
    _require(isinstance(data, dict), f"최상위가 object가 아님: {path}")
    return data


def _num(value: object, where: str) -> float:
    # bool은 int 서브타입이라 명시적으로 배제(타입 안전).
    _require(isinstance(value, Real) and not isinstance(value, bool), f"숫자가 아님: {where} = {value!r}")
    return float(value)


def _str(value: object, where: str) -> str:
    _require(isinstance(value, str) and value != "", f"비어있지 않은 문자열 필요: {where} = {value!r}")
    return value


def _bool(value: object, where: str) -> bool:
    _require(isinstance(value, bool), f"bool 필요: {where} = {value!r}")
    return value


def _opt_limit(value: object, where: str, *, integer: bool = False) -> float | int | None:
    """손실 한도: number 또는 "data_missing"(→ None). 그 외 → 에러(fail-closed)."""
    if value == _DATA_MISSING:
        return None
    n = _num(value, where)
    return int(n) if integer else n


# --- universe ---


def _build_universe(data: dict, path: Path) -> UniversePolicy:
    tickers = data.get("tickers")
    _require(isinstance(tickers, list) and len(tickers) > 0, f"tickers 비어있음/없음: {path}")

    entries: list[TierEntry] = []
    for i, t in enumerate(tickers):
        where = f"{path.name}.tickers[{i}]"
        _require(isinstance(t, dict), f"ticker가 object가 아님: {where}")
        symbol = _str(t.get("symbol"), f"{where}.symbol")
        primary_tier = _str(t.get("primary_tier"), f"{where}.primary_tier")
        tiers_raw = t.get("tiers")
        _require(
            isinstance(tiers_raw, list) and len(tiers_raw) > 0
            and all(isinstance(x, str) for x in tiers_raw),
            f"tiers는 비어있지 않은 list[str]: {where}",
        )
        status = _str(t.get("status"), f"{where}.status")
        _require(status in VALID_STATUSES, f"알 수 없는 status '{status}': {where}")
        entries.append(
            TierEntry(
                symbol=symbol,
                primary_tier=primary_tier,
                tiers=tuple(tiers_raw),
                status=status,
                tradable=_bool(t.get("tradable"), f"{where}.tradable"),
                leveraged=_bool(t.get("leveraged"), f"{where}.leveraged"),
            )
        )
    return UniversePolicy(entries=tuple(entries))


# --- risk modes + guards ---


def _build_risk_modes(data: dict, path: Path) -> tuple[RiskMode, ...]:
    modes_raw = data.get("risk_modes")
    _require(isinstance(modes_raw, dict) and len(modes_raw) > 0, f"risk_modes 비어있음/없음: {path}")

    whitelist_raw = data.get("c_mode_tier2_whitelist", [])
    _require(
        isinstance(whitelist_raw, list) and all(isinstance(x, str) for x in whitelist_raw),
        f"c_mode_tier2_whitelist는 list[str]: {path}",
    )
    whitelist = tuple(whitelist_raw)

    modes: list[RiskMode] = []
    for name, m in modes_raw.items():
        where = f"{path.name}.risk_modes.{name}"
        _require(isinstance(m, dict), f"mode가 object가 아님: {where}")
        allowed_raw = m.get("allowed_tiers")
        _require(
            isinstance(allowed_raw, list) and all(isinstance(x, str) for x in allowed_raw),
            f"allowed_tiers는 list[str]: {where}",
        )
        cap = _num(m.get("max_account_loss_pct_per_trade"), f"{where}.max_account_loss_pct_per_trade")
        _require(cap >= 0, f"account_loss_cap 음수 불가: {where} = {cap}")
        whitelist_only = _bool(m.get("tier2_whitelist_only", False), f"{where}.tier2_whitelist_only")
        modes.append(
            RiskMode(
                name=name,
                account_loss_cap=cap,
                allowed_tiers=tuple(allowed_raw),
                tier2_whitelist_only=whitelist_only,
                tier2_whitelist=whitelist if whitelist_only else (),
                default=_bool(m.get("default"), f"{where}.default"),
            )
        )

    defaults = [m for m in modes if m.default]
    _require(len(defaults) == 1, f"정확히 하나의 default=True 모드가 필요: {path} (현재 {len(defaults)}개)")
    return tuple(modes)


def _build_guards(data: dict, path: Path) -> PortfolioGuards:
    pg = data.get("portfolio_guards")
    _require(isinstance(pg, dict), f"portfolio_guards 없음/object 아님: {path}")
    return PortfolioGuards(
        mdd_hard_stop_pct=_num(pg.get("mdd_hard_stop_pct"), f"{path.name}.portfolio_guards.mdd_hard_stop_pct"),
        daily_loss_limit_pct=_opt_limit(pg.get("daily_loss_limit_pct"), f"{path.name}.portfolio_guards.daily_loss_limit_pct"),
        weekly_loss_limit_pct=_opt_limit(pg.get("weekly_loss_limit_pct"), f"{path.name}.portfolio_guards.weekly_loss_limit_pct"),
        consecutive_loss_limit_count=_opt_limit(
            pg.get("consecutive_loss_limit_count"),
            f"{path.name}.portfolio_guards.consecutive_loss_limit_count",
            integer=True,
        ),
    )


# --- 공개 진입점 ---


def load_policy(config_dir: str | Path | None = None) -> Policy:
    """config/*.json 을 읽어 검증하고 순수 Policy 모델로 빌드한다(fail-closed).

    config_dir=None이면 레포 config/. 문제가 있으면 PolicyConfigError.
    """
    base = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR
    _require(base.is_dir(), f"config 디렉토리 없음: {base}")

    universe_path = base / "universe_tiers.json"
    risk_path = base / "risk_profiles.json"

    universe_data = _read_json(universe_path)
    risk_data = _read_json(risk_path)

    return Policy(
        universe=_build_universe(universe_data, universe_path),
        risk_modes=_build_risk_modes(risk_data, risk_path),
        portfolio_guards=_build_guards(risk_data, risk_path),
    )
