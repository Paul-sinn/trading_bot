"""policy_loader 테스트 (spec: specs/policy_loader.md).

config/*.json → 순수 Policy 모델 로드 + 검증 + fail-closed를 본다. 네트워크 없음. malformed config는
tmp_path에 직접 써서 검증한다. 실제 레포 config/ 를 로드해 SSOT 일관성도 1개 통합 테스트로 확인한다.
윈도우 cp949 회피 위해 파일 쓰기/읽기에 encoding="utf-8" 명시.
"""

import json
from pathlib import Path

import pytest

from algorithms.policy import (
    ConcentrationPolicy,
    Policy,
    PortfolioGuards,
    RiskMode,
    UniversePolicy,
)
from agents.policy_loader import PolicyConfigError, load_policy

ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = ROOT / "config"


# --- 유효한 최소 config 생성 헬퍼 ---


def _good_universe() -> dict:
    return {
        "_meta": {"kind": "DRAFT"},
        "tickers": [
            {"symbol": "NVDA", "primary_tier": "1", "tiers": ["1"], "status": "approved",
             "tradable": True, "leveraged": False},
            {"symbol": "PLTR", "primary_tier": "2", "tiers": ["2"], "status": "approved",
             "tradable": True, "leveraged": False},
            {"symbol": "SPY", "primary_tier": "0", "tiers": ["0"], "status": "approved",
             "tradable": False, "leveraged": False},
        ],
    }


def _good_risk() -> dict:
    return {
        "risk_modes": {
            "B": {"default": True, "max_account_loss_pct_per_trade": 0.07,
                  "allowed_tiers": ["0", "1", "2", "3", "4A", "4B"]},
            "C": {"default": False, "max_account_loss_pct_per_trade": 0.10,
                  "allowed_tiers": ["0", "1", "2"], "tier2_whitelist_only": True},
        },
        "c_mode_tier2_whitelist": ["PLTR", "COIN", "HOOD", "VRT", "CRWD", "ARM", "MU"],
        "portfolio_guards": {
            "mdd_hard_stop_pct": 0.20,
            "daily_loss_limit_pct": "data_missing",
            "weekly_loss_limit_pct": "data_missing",
            "consecutive_loss_limit_count": "data_missing",
        },
    }


def _write_config(tmp: Path, universe: dict, risk: dict) -> Path:
    (tmp / "universe_tiers.json").write_text(
        json.dumps(universe, ensure_ascii=False), encoding="utf-8"
    )
    (tmp / "risk_profiles.json").write_text(
        json.dumps(risk, ensure_ascii=False), encoding="utf-8"
    )
    return tmp


# --- happy path ---


def test_load_minimal_good_config(tmp_path):
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    policy = load_policy(cfg)
    assert isinstance(policy, Policy)
    assert isinstance(policy.universe, UniversePolicy)
    assert {m.name for m in policy.risk_modes} == {"B", "C"}


def test_modes_mapped_correctly(tmp_path):
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    policy = load_policy(cfg)
    b, c = policy.mode("B"), policy.mode("C")
    assert b.account_loss_cap == 0.07 and b.default is True
    assert b.tier2_whitelist_only is False and b.tier2_whitelist == ()
    assert c.account_loss_cap == 0.10 and c.default is False
    assert c.tier2_whitelist_only is True
    assert set(c.tier2_whitelist) == {"PLTR", "COIN", "HOOD", "VRT", "CRWD", "ARM", "MU"}
    assert "SMCI" not in c.tier2_whitelist  # SSOT: 감사로 제외 유지


def test_default_mode_property(tmp_path):
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    policy = load_policy(cfg)
    assert policy.default_mode is not None
    assert policy.default_mode.name == "B"
    assert policy.mode("NOPE") is None


def test_universe_entries_built(tmp_path):
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    policy = load_policy(cfg)
    nvda = policy.universe.get("NVDA")
    assert nvda is not None and nvda.tradable is True and nvda.status == "approved"
    assert policy.universe.get("SPY").tradable is False


def test_portfolio_guards_data_missing_to_none(tmp_path):
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    pg = load_policy(cfg).portfolio_guards
    assert isinstance(pg, PortfolioGuards)
    assert pg.mdd_hard_stop_pct == 0.20
    assert pg.daily_loss_limit_pct is None
    assert pg.weekly_loss_limit_pct is None
    assert pg.consecutive_loss_limit_count is None


def test_portfolio_guards_numeric_limits_preserved(tmp_path):
    risk = _good_risk()
    risk["portfolio_guards"]["daily_loss_limit_pct"] = 0.03
    risk["portfolio_guards"]["consecutive_loss_limit_count"] = 3
    cfg = _write_config(tmp_path, _good_universe(), risk)
    pg = load_policy(cfg).portfolio_guards
    assert pg.daily_loss_limit_pct == 0.03
    assert pg.consecutive_loss_limit_count == 3


# --- fail-closed: malformed / missing ---


def test_missing_dir_raises(tmp_path):
    with pytest.raises(PolicyConfigError):
        load_policy(tmp_path / "does_not_exist")


def test_invalid_json_raises(tmp_path):
    (tmp_path / "universe_tiers.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "risk_profiles.json").write_text(
        json.dumps(_good_risk()), encoding="utf-8"
    )
    with pytest.raises(PolicyConfigError):
        load_policy(tmp_path)


def test_empty_tickers_raises(tmp_path):
    u = _good_universe()
    u["tickers"] = []
    cfg = _write_config(tmp_path, u, _good_risk())
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_invalid_status_raises(tmp_path):
    u = _good_universe()
    u["tickers"][0]["status"] = "totally_made_up"
    cfg = _write_config(tmp_path, u, _good_risk())
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_missing_ticker_field_raises(tmp_path):
    u = _good_universe()
    del u["tickers"][0]["tradable"]
    cfg = _write_config(tmp_path, u, _good_risk())
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_no_default_mode_raises(tmp_path):
    risk = _good_risk()
    risk["risk_modes"]["B"]["default"] = False  # 둘 다 default=False
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_multiple_default_modes_raises(tmp_path):
    risk = _good_risk()
    risk["risk_modes"]["C"]["default"] = True  # B,C 둘 다 default=True
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_bad_account_loss_cap_raises(tmp_path):
    risk = _good_risk()
    risk["risk_modes"]["B"]["max_account_loss_pct_per_trade"] = "lots"
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_missing_mdd_hard_stop_raises(tmp_path):
    risk = _good_risk()
    del risk["portfolio_guards"]["mdd_hard_stop_pct"]
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


def test_bad_loss_limit_string_raises(tmp_path):
    risk = _good_risk()
    risk["portfolio_guards"]["daily_loss_limit_pct"] = "soon"  # data_missing 아닌 문자열
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)


# --- 실제 레포 config/ 통합(SSOT 일관성) ---


def test_load_real_repo_config():
    policy = load_policy(REAL_CONFIG)
    assert len(policy.universe.entries) == 63
    assert policy.default_mode.name == "B"
    c = policy.mode("C")
    assert c.account_loss_cap == 0.10
    assert "SMCI" not in c.tier2_whitelist          # 감사 제외 유지
    assert len(c.tier2_whitelist) == 7
    assert policy.portfolio_guards.mdd_hard_stop_pct == 0.20
    assert policy.portfolio_guards.daily_loss_limit_pct is None  # data_missing


# --- concentration_phases 파싱 ---


def test_concentration_optional_when_missing(tmp_path):
    # 최소 config(_good_risk엔 concentration_phases 없음) → 빈 ConcentrationPolicy(로드 성공).
    cfg = _write_config(tmp_path, _good_universe(), _good_risk())
    conc = load_policy(cfg).concentration
    assert isinstance(conc, ConcentrationPolicy)
    assert conc.phases == ()


def test_concentration_parsed_from_config(tmp_path):
    risk = _good_risk()
    risk["concentration_phases"] = {
        "1": {"account_usd": [1000, 3000], "mode": "concentrated",
              "deploy_caps_pct": {"tier_0_2": [80, 100], "tier_4B": [50, 70],
                                  "tier_5": "small_only", "tier_6": "conservative_per_tier_2_or_5"}},
        "2": {"account_usd": [3000, 5000], "main_pct": [60, 80]},
    }
    cfg = _write_config(tmp_path, _good_universe(), risk)
    conc = load_policy(cfg).concentration
    p1 = conc.phase("1")
    assert p1.tier_caps["tier_0_2"].low == pytest.approx(0.80)
    assert p1.tier_caps["tier_4B"].low == pytest.approx(0.50)   # 더 낮은 캡
    assert p1.tier_caps["tier_5"].special == "small_only"
    assert p1.tier_caps["tier_6"].special == "conservative"
    assert conc.phase("2").single_position_low == pytest.approx(0.60)


def test_real_config_concentration_has_phase1_tiers():
    conc = load_policy(REAL_CONFIG).concentration
    p1 = conc.phase("1")
    assert p1 is not None
    assert p1.tier_caps["tier_5"].special == "small_only"   # Tier5 집중 금지
    assert p1.tier_caps["tier_4B"].low < p1.tier_caps["tier_0_2"].low  # 4B 더 낮음


def test_bad_deploy_cap_raises(tmp_path):
    risk = _good_risk()
    risk["concentration_phases"] = {
        "1": {"account_usd": [1000, 3000], "deploy_caps_pct": {"tier_0_2": "nonsense_value"}},
    }
    # "small_only"/"conservative" 외 임의 문자열은 conservative로 흡수되지만, 숫자도 문자열도 아닌 형식은 거부.
    risk["concentration_phases"]["1"]["deploy_caps_pct"]["tier_3"] = {"bad": "dict"}
    cfg = _write_config(tmp_path, _good_universe(), risk)
    with pytest.raises(PolicyConfigError):
        load_policy(cfg)
