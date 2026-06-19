"""알고리즘 — 룰 기반 point-in-time 유니버스 선정 (순수 함수, 생존편향 제거).

헌장 docs/STRATEGY.md §3: 손으로 승자를 고르면 백테스트가 부풀어 거짓말한다(생존편향). 선정은 룰 기반
point-in-time — 각 역사 시점의 자격 종목을 유동성·ATR 밴드로 선정하고 레버리지/인버스를 제외하며,
상장폐지 종목도 상폐 이전 시점엔 포함해 생존편향을 제거한다.

ADR-002: 부수효과 없는 순수 함수. I/O·네트워크·DB·전역상태·난수 금지. 입력만으로 출력 결정.
미래참조 금지: as_of에 미상장(상장 전)·기상폐 종목을 넣지 않되, 상폐 이전 시점엔 반드시 포함한다.

spec: specs/universe.md
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolMetrics:
    """시점별 종목 메트릭(point-in-time). 날짜는 ISO 'YYYY-MM-DD' 문자열."""

    listed_from: str
    delisted_at: str | None
    avg_dollar_volume: float
    atr_pct: float
    is_leveraged_or_inverse: bool


def select_universe(
    metrics: dict[str, SymbolMetrics],
    as_of: str,
    *,
    min_dollar_volume: float,
    atr_pct_band: tuple[float, float] = (0.015, 0.05),
    exclude_leveraged: bool = True,
) -> list[str]:
    """as_of 시점에 자격을 갖춘 심볼을 정렬해 반환한다 (헌장 §3 룰 기반 point-in-time).

    포함(모두 AND): 상장됨(listed_from<=as_of) AND 미상폐(delisted_at None 또는 as_of<delisted_at)
    AND 유동성≥min AND ATR%∈band(폐구간) AND (레버리지/인버스 제외 옵션). 날짜는 ISO 사전식 비교.
    """
    low, high = atr_pct_band
    selected: list[str] = []
    for symbol, m in metrics.items():
        if m.listed_from > as_of:  # 미상장(미래참조 금지)
            continue
        if m.delisted_at is not None and as_of >= m.delisted_at:  # 기상폐(상폐일 당일 제외)
            continue
        if m.avg_dollar_volume < min_dollar_volume:  # 유동성 부족
            continue
        if not (low <= m.atr_pct <= high):  # 변동성 밴드 밖
            continue
        if exclude_leveraged and m.is_leveraged_or_inverse:  # 레버리지/인버스
            continue
        selected.append(symbol)
    return sorted(selected)
