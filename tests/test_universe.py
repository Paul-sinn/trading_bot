"""Phase 5 step11 — 룰 기반 point-in-time 유니버스 선정 테스트 (TDD Red→Green).

spec: specs/universe.md  ·  헌장: §3(생존편향 제거·point-in-time)
- 순수 함수. 상폐종목은 상폐 전 시점엔 포함(생존편향 제거), 후엔 제외. 미상장 제외(미래참조 금지).
"""

from algorithms.universe import SymbolMetrics, select_universe


def _m(
    listed_from="2010-01-01",
    delisted_at=None,
    avg_dollar_volume=1e8,
    atr_pct=0.03,
    is_leveraged_or_inverse=False,
) -> SymbolMetrics:
    return SymbolMetrics(
        listed_from=listed_from,
        delisted_at=delisted_at,
        avg_dollar_volume=avg_dollar_volume,
        atr_pct=atr_pct,
        is_leveraged_or_inverse=is_leveraged_or_inverse,
    )


def _base() -> dict[str, SymbolMetrics]:
    return {
        "AAA": _m(),  # 정상 현존
        "DEAD": _m(delisted_at="2019-06-01"),  # 2019-06-01 상폐
        "NEW": _m(listed_from="2021-01-01"),  # 2021 상장
        "LEV": _m(is_leveraged_or_inverse=True),  # 레버리지
        "ILLIQ": _m(avg_dollar_volume=1e5),  # 유동성 부족
        "DULL": _m(atr_pct=0.005),  # 변동성 너무 낮음
        "WILD": _m(atr_pct=0.20),  # 변동성 너무 높음
    }


# --- 생존편향 제거: 상폐종목 시점 처리 ---


def test_delisted_included_before_delisting():
    # 2018(상폐 전) → DEAD 포함(생존편향 제거 — 빼면 부풀려짐).
    u = select_universe(_base(), "2018-01-01", min_dollar_volume=1e7)
    assert "DEAD" in u


def test_delisted_excluded_after_delisting():
    # 2020(상폐 후) → DEAD 제외.
    u = select_universe(_base(), "2020-01-01", min_dollar_volume=1e7)
    assert "DEAD" not in u


def test_delisting_day_is_excluded():
    u = select_universe(_base(), "2019-06-01", min_dollar_volume=1e7)
    assert "DEAD" not in u  # 상폐일 당일 제외(<)


# --- 미래참조 금지: 미상장 제외 ---


def test_not_yet_listed_excluded():
    # 2020 시점에 2021 상장 NEW는 없음(미래참조 금지).
    u = select_universe(_base(), "2020-01-01", min_dollar_volume=1e7)
    assert "NEW" not in u


def test_listed_from_boundary_included():
    u = select_universe({"X": _m(listed_from="2020-01-01")}, "2020-01-01", min_dollar_volume=1e7)
    assert "X" in u


# --- 룰 필터 ---


def test_leveraged_excluded():
    u = select_universe(_base(), "2022-01-01", min_dollar_volume=1e7)
    assert "LEV" not in u


def test_illiquid_excluded():
    u = select_universe(_base(), "2022-01-01", min_dollar_volume=1e7)
    assert "ILLIQ" not in u


def test_atr_band_filters_dull_and_wild():
    u = select_universe(_base(), "2022-01-01", min_dollar_volume=1e7, atr_pct_band=(0.015, 0.05))
    assert "DULL" not in u and "WILD" not in u
    assert "AAA" in u


def test_atr_band_boundaries_inclusive():
    metrics = {"LO": _m(atr_pct=0.015), "HI": _m(atr_pct=0.05)}
    u = select_universe(metrics, "2022-01-01", min_dollar_volume=1e7, atr_pct_band=(0.015, 0.05))
    assert "LO" in u and "HI" in u


# --- 엣지 / 결정론 ---


def test_empty_metrics_returns_empty():
    assert select_universe({}, "2022-01-01", min_dollar_volume=1e7) == []


def test_result_is_sorted_deterministic():
    u = select_universe(_base(), "2022-01-01", min_dollar_volume=1e7)
    assert u == sorted(u)
