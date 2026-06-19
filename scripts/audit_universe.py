"""유니버스 티어 감사 도구 (read-only — 주문/전략 로직 없음, live greenlight 아님).

`docs/UNIVERSE_TIERS.md` §7의 정량 감사를 재현한다. 각 티커를 Norgate 실데이터(NDU 로컬)로 확인:
존재/활성, listed_from, ADV($M, 63일 Turnover 평균), ATR%(Wilder ATR14/종가), 레버리지여부, 이름/타입.

전략 성과 검증이 아니라 **universe audit**이다. NorgateProvider._metrics_for(테스트된 경로)를 재사용한다.
NDU가 켜져 있어야 한다(미기동 시 norgatedata가 재시도 후 실패). 트라이얼은 ~2년 커버리지라 listed_from이
트라이얼 시작일로 잘릴 수 있음(실 IPO일 ≠).

사용:  $env:PYTHONPATH="."; .venv\Scripts\python.exe scripts\audit_universe.py
"""

import sys

sys.path.insert(0, ".")
import norgatedata as ng

from agents.data_adapter import NorgateProvider

AS_OF = "2026-06-18"
TICKERS = [
    # Tier 0 (compass/regime only)
    "SPY", "QQQ", "SMH", "SOXX", "XLK", "$VIX",
    # Tier 1
    "NVDA", "AVGO", "AMD", "MSFT", "META", "GOOGL", "AMZN", "AAPL",
    # Tier 2
    "PLTR", "COIN", "HOOD", "VRT", "SMCI", "CRWD", "ARM", "MU", "NET",
    "DDOG", "HIMS", "SNOW", "MDB", "TSM", "ASML",
    # Tier 3
    "ETN", "GEV", "CEG", "NRG", "PWR", "EME", "FIX", "DOV",
    # Tier 4A
    "LMT", "RTX", "NOC", "GD", "BA",
    # Tier 4B
    "SPCX", "RKLB", "ASTS", "LUNR", "PL",
    # Tier 5
    "IONQ", "RGTI", "QBTS", "SOUN", "BBAI", "AI", "PATH", "SYM", "SERV",
    "APLD", "IREN", "CORZ",
    # Tier 6
    "MSTR", "MARA", "RIOT", "CLSK",
]


def main() -> None:
    prov = NorgateProvider()
    print(
        f"{'ticker':8}{'found':6}{'listed_from':13}{'delisted':11}"
        f"{'ADV($M)':>10}{'ATR%':>8}{'lev':>5}  name / subtype"
    )
    print("-" * 110)
    for t in TICKERS:
        try:
            name = ng.security_name(t)
            sub = ng.subtype1(t)
        except Exception:
            name, sub = None, None
        if name is None:
            print(f"{t:8}{'NO':6}{'-':13}{'-':11}{'-':>10}{'-':>8}{'-':>5}  (NOT FOUND in Norgate)")
            continue
        try:
            m = prov._metrics_for(t, AS_OF)
        except Exception as e:  # noqa: BLE001 — 감사 도구, 개별 실패는 표시만
            print(f"{t:8}{'err':6} {type(e).__name__}: {e}  | {name} / {sub}")
            continue
        if m is None:
            print(f"{t:8}{'NODATA':6}{'-':13}{'-':11}{'-':>10}{'-':>8}{'-':>5}  {name} / {sub} (no price rows)")
            continue
        adv_m = m.avg_dollar_volume / 1e6
        print(
            f"{t:8}{'yes':6}{str(m.listed_from):13}{str(m.delisted_at or '-'):11}"
            f"{adv_m:>10.1f}{m.atr_pct * 100:>7.1f}%"
            f"{('Y' if m.is_leveraged_or_inverse else 'n'):>5}  {name} / {sub}"
        )


if __name__ == "__main__":
    main()
