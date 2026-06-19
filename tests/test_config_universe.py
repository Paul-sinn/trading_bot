"""정책 검증 테스트 — config/universe_tiers.json·risk_profiles.json 의 형식·일관성만 검증한다.

⚠️ 성과 검증/전략 로직 검증이 아니다. universe audit 단계 산출물(config 드래프트)이 source of truth
(docs/overnight_constitution_and_universe_audit.md)와 일관되고 잘 형성됐는지만 본다. 네트워크 없음.
윈도우 cp949 회피 위해 encoding="utf-8" 명시.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"

STATUS_VALUES = {"approved", "watch", "needs_review", "reject", "data_missing"}
COVERAGE_VALUES = {"ok_2y_trial", "insufficient_ipo", "data_missing"}


def _load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def _universe() -> dict:
    return _load("universe_tiers.json")


def _tickers() -> list[dict]:
    return _universe()["tickers"]


# --- universe_tiers.json ---


def test_universe_json_parses_and_has_tickers():
    u = _universe()
    assert isinstance(u["tickers"], list) and len(u["tickers"]) == 63  # 63 unique


def test_every_ticker_has_valid_fields():
    for t in _tickers():
        assert t["status"] in STATUS_VALUES, t
        assert t["data_coverage"] in COVERAGE_VALUES, t
        assert t["tiers"], t  # 비어있지 않음
        assert t["primary_tier"] in t["tiers"], t
        assert isinstance(t["leveraged"], bool)
        assert isinstance(t["tradable"], bool)


def test_tier0_is_not_tradable_rest_is():
    for t in _tickers():
        if t["primary_tier"] == "0":
            assert t["tradable"] is False, t  # 컴퍼스 전용
        else:
            assert t["tradable"] is True, t


def test_no_leveraged_inverse_in_universe():
    # 유니버스엔 레버리지/인버스 ETF가 없어야 한다(헌장 §3 OFF). BBAI 오탐은 false로 정정됨.
    assert all(t["leveraged"] is False for t in _tickers())
    bbai = next(t for t in _tickers() if t["symbol"] == "BBAI")
    assert bbai["leveraged"] is False  # 'BigBEAR' 휴리스틱 오탐 정정


def test_status_counts_match_audit_report():
    counts: dict[str, int] = {}
    for t in _tickers():
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    assert counts.get("approved") == 35
    assert counts.get("watch") == 21
    assert counts.get("needs_review") == 7
    assert counts.get("reject", 0) == 0


def test_needs_review_set_is_explicit():
    nr = {t["symbol"] for t in _tickers() if t["status"] == "needs_review"}
    assert nr == {"SMCI", "SPCX", "LUNR", "BBAI", "AI", "SYM", "SERV"}


def test_spcx_marked_insufficient_data():
    spcx = next(t for t in _tickers() if t["symbol"] == "SPCX")
    assert spcx["status"] == "needs_review"
    assert spcx["data_coverage"] == "insufficient_ipo"


def test_multitag_consistency():
    by_sym = {t["symbol"]: t for t in _tickers()}
    for sym in ("COIN", "HOOD"):
        assert "6" in by_sym[sym]["tiers"] and by_sym[sym]["primary_tier"] == "2"
    for sym in ("IREN", "CORZ"):
        assert "6" in by_sym[sym]["tiers"] and by_sym[sym]["primary_tier"] == "5"


# --- risk_profiles.json ---


def test_risk_modes_caps_and_tiers():
    r = _load("risk_profiles.json")
    b, c = r["risk_modes"]["B"], r["risk_modes"]["C"]
    assert b["default"] is True and c["default"] is False
    assert b["max_account_loss_pct_per_trade"] == 0.07
    assert c["max_account_loss_pct_per_trade"] == 0.10
    assert set(c["allowed_tiers"]) <= {"0", "1", "2"}  # C는 Tier 0~2만


def test_c_mode_whitelist_is_tier2_and_excludes_smci():
    r = _load("risk_profiles.json")
    wl = set(r["c_mode_tier2_whitelist"])
    tier2 = {t["symbol"] for t in _tickers() if t["primary_tier"] == "2"}
    assert wl <= tier2  # whitelist ⊆ Tier 2
    assert "SMCI" not in wl  # 감사 결과 제외
    # 제거 기록 명시
    removed = {x["symbol"] for x in r["c_mode_tier2_whitelist_removed"]}
    assert "SMCI" in removed


def test_portfolio_mdd_hard_stop_and_missing_limits_flagged():
    r = _load("risk_profiles.json")
    pg = r["portfolio_guards"]
    assert pg["mdd_hard_stop_pct"] == 0.20  # 불변
    # 미정 한도는 data_missing 으로 명시(추측 금지)
    assert pg["daily_loss_limit_pct"] == "data_missing"
    assert pg["weekly_loss_limit_pct"] == "data_missing"
    assert pg["consecutive_loss_limit_count"] == "data_missing"


def test_concentration_phases_present():
    r = _load("risk_profiles.json")
    assert set(r["concentration_phases"]) == {"1", "2", "3", "4"}


def test_tier5_profile_forbids_concentration_and_cmode():
    r = _load("risk_profiles.json")
    rules = " ".join(r["tier5_profile"]["rules"]).lower()
    assert "no concentration" in rules and "no c mode" in rules


# --- 드래프트/템플릿 존재 ---


def test_drafts_and_template_exist_and_marked_draft():
    assert (CONFIG / "README.md").exists()
    u = _universe()
    assert "DRAFT" in u["_meta"]["kind"]
    tmpl = (ROOT / "docs" / "templates" / "decision_report_template.md").read_text(encoding="utf-8")
    assert "DRY-RUN" in tmpl and "TEMPLATE" in tmpl
    assert "orders_placed" in tmpl  # 항상 0 불변식 명시
