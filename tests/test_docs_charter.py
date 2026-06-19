"""헌장 개정(2026-06-19) 문서 검증 — 코드 아닌 정책 불변식을 문서 레벨에서 고정한다.

deliverable: 티어 기반 유니버스 + risk-gated concentration + No return guarantee + 자동 라이브 금지가
헌장/티어문서/ADR에 실제로 박혀 있는지 검증. 네트워크/데이터 없음. 윈도우 cp949 회피 위해 utf-8 명시.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_universe_tiers_doc_exists_and_has_six_tiers():
    text = _read("UNIVERSE_TIERS.md")
    for tier in ("Tier 0", "Tier 1", "Tier 2", "Tier 3", "Tier 4A", "Tier 4B", "Tier 5", "Tier 6"):
        assert tier in text, f"{tier} 누락"
    # 컴퍼스 전용 / 매매 분리 명시
    assert "Compass" in text or "컴퍼스" in text


def test_risk_modes_and_concentration_defined():
    text = _read("UNIVERSE_TIERS.md")
    assert "B Mode" in text and "C Mode" in text
    # 거래당 계좌손실 한도 (ASCII '-' 또는 유니코드 '−' U+2212 둘 다 허용)
    assert ("-7%" in text or "−7%" in text) and ("-10%" in text or "−10%" in text)
    assert "Concentration" in text
    # capital deployed ≠ capital at risk 공식
    assert "position_weight" in text and "stop_loss_pct" in text


def test_charter_has_no_return_guarantee_clause():
    text = _read("STRATEGY.md")
    assert "No return guarantee" in text
    # 보조 evidence는 RiskGate를 override 못 한다(불변)
    assert "override" in text


def test_charter_references_tiers_doc():
    assert "UNIVERSE_TIERS.md" in _read("STRATEGY.md")


def test_adr_records_universe_revision():
    text = _read("ADR.md")
    assert "ADR-012" in text
    assert "risk-gated concentration" in text.lower() or "risk-gated concentration" in text


def test_no_auto_live_order_policy_preserved():
    # 자동 라이브 주문 금지 문구 유지(헌장 §10 / 티어문서 §0).
    tiers = _read("UNIVERSE_TIERS.md")
    assert "자동 주문" in tiers or "라이브 실행 코드" in tiers
