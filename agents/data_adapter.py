"""일봉 OHLCV 데이터 어댑터 (I/O — agents 레이어).

헌장 docs/STRATEGY.md §3/§10: v1 백테스트·라이브가 쓸 일봉 OHLCV + SPY + VIX를 무료 소스에서 받아
백테스트 엔진(step5)이 먹는 DataFrame 형식으로 정규화한다. 소스는 주입형(provider).

⚠️ I/O라 순수 함수가 아니다 — agents에 둔다(ADR-001: 외부 API·I/O는 backend/agents에만, algorithms는
순수 유지 ADR-002). 네트워크 호출은 provider 내부(_default_fetch)에만. 실제 SDK는 지연 import.
시크릿·키를 로그에 노출하지 않는다. Robinhood 과거 데이터는 백테스트 소스로 쓰지 않는다(헌장 §3).

spec: specs/data_adapter.md
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

import pandas as pd

from algorithms.universe import SymbolMetrics, select_universe

SURVIVORSHIP_WARNING = (
    "⚠️ 무료 일봉 소스는 상장폐지 종목이 없어 생존편향이 내장된다. v1 한정 '낙관적 상한'이며 "
    "라이브 전 생존편향 없는 벤더(상폐종목+시점별 지수편입)로 point-in-time 재검증이 필요하다(헌장 §3)."
)


@runtime_checkable
class DailyDataProvider(Protocol):
    """일봉 OHLCV/VIX 조회 인터페이스(동기, 배치 과거데이터). 구현은 Mock/무료소스로 분기."""

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame: ...

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series: ...


def normalize_ohlcv(
    raw: pd.DataFrame,
    price_col_priority: tuple[str, ...] = ("adj close", "close"),
) -> pd.DataFrame:
    """원시 OHLCV를 백테스트 엔진 형식으로 정규화한다(네트워크 없음).

    컬럼명 대소문자 무시 매핑 → open/high/low/close/volume(float64). close는 우선순위로 수정주가 채택.
    인덱스 오름차순 정렬·중복 제거·dropna. 필수 컬럼 누락 시 KeyError.

    yfinance가 단일 심볼도 MultiIndex 컬럼(('Close','AAPL') 등)으로 줄 수 있어, MultiIndex면
    필드명 레벨(level 0)로 평탄화한다.
    """
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = raw.columns.get_level_values(0)

    lowered = {str(c).strip().lower(): c for c in raw.columns}

    def pick(name: str) -> str:
        if name not in lowered:
            raise KeyError(f"필수 컬럼 누락: '{name}' (있는 컬럼: {list(raw.columns)})")
        return lowered[name]

    close_src = next((lowered[c] for c in price_col_priority if c in lowered), None)
    if close_src is None:
        raise KeyError(f"close/adj close 컬럼 누락 (있는 컬럼: {list(raw.columns)})")

    out = pd.DataFrame(
        {
            "open": raw[pick("open")],
            "high": raw[pick("high")],
            "low": raw[pick("low")],
            "close": raw[close_src],
            "volume": raw[pick("volume")],
        }
    )
    out = out.astype("float64")
    out = out[~out.index.duplicated(keep="last")].sort_index().dropna()
    return out


class MockDailyProvider:
    """결정론적 합성 데이터 provider (테스트용, 네트워크·난수 없음)."""

    def __init__(
        self, frames: dict[str, pd.DataFrame], vix: pd.Series | None = None
    ) -> None:
        self._frames = {s: normalize_ohlcv(df) for s, df in frames.items()}
        self._vix = pd.Series(vix, dtype="float64") if vix is not None else pd.Series(dtype="float64")

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        if symbol not in self._frames:
            raise KeyError(f"데이터 없음: {symbol!r}")
        df = self._frames[symbol]
        if start is not None or end is not None:
            df = df.loc[start:end]
        return df

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series:
        vix = self._vix
        if start is not None or end is not None:
            vix = vix.loc[start:end]
        return vix


class FreeDailyProvider:
    """무료 일봉 소스(yfinance/Stooq 등) 어댑터. 네트워크는 _default_fetch에만.

    fetch_fn/vix_fetch_fn 주입 시 그것을 사용(테스트는 네트워크 없이 정규화 검증).
    미주입 시 지연 import한 yfinance로 조회한다.
    """

    survivorship_biased: bool = True

    def __init__(
        self,
        *,
        fetch_fn: Callable[[str, str | None, str | None], pd.DataFrame] | None = None,
        vix_fetch_fn: Callable[[str, str | None, str | None], pd.DataFrame] | None = None,
        vix_symbol: str = "^VIX",
    ) -> None:
        self._fetch_fn = fetch_fn
        self._vix_fetch_fn = vix_fetch_fn
        self._vix_symbol = vix_symbol

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        fetch = self._fetch_fn or self._default_fetch
        return normalize_ohlcv(fetch(symbol, start, end))

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series:
        fetch = self._vix_fetch_fn or self._fetch_fn or self._default_fetch
        return normalize_ohlcv(fetch(self._vix_symbol, start, end))["close"]

    @staticmethod
    def _default_fetch(
        symbol: str, start: str | None, end: str | None
    ) -> pd.DataFrame:
        """실 네트워크 조회 — 지연 import(미설치 시 명확한 안내)."""
        try:
            import yfinance  # 지연 import: 미설치 환경에서 모듈 전체가 죽지 않게.
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise ImportError(
                "yfinance 미설치. `pip install yfinance` 후 사용하거나 fetch_fn을 주입하라."
            ) from exc
        return yfinance.download(
            symbol, start=start, end=end, auto_adjust=False, progress=False
        )


# ===========================================================================
# step11: 생존편향 없는 point-in-time 데이터 (상폐종목 포함, 소스 비종속)
# ===========================================================================


@runtime_checkable
class PointInTimeProvider(Protocol):
    """생존편향 없는 point-in-time 조회 인터페이스(상폐종목 포함).

    get_ohlcv/get_vix는 DailyDataProvider와 호환(run_v1 재사용 가능). 상폐종목도 OHLCV를 돌려준다.
    """

    def get_metrics(self, as_of: str) -> dict[str, SymbolMetrics]: ...

    def get_constituents(self, as_of: str) -> list[str]: ...

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame: ...

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series: ...


class MockPointInTimeProvider:
    """결정론적 point-in-time provider (테스트용, 네트워크 없음).

    상장폐지 종목·약세장 데이터를 포함하는 합성 데이터로 생존편향 제거 경로를 검증한다.
    """

    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        metrics: dict[str, SymbolMetrics],
        vix: pd.Series,
        *,
        min_dollar_volume: float = 1e7,
        atr_pct_band: tuple[float, float] = (0.015, 0.05),
    ) -> None:
        self._frames = {s: normalize_ohlcv(df) for s, df in frames.items()}
        self._metrics = dict(metrics)
        self._vix = pd.Series(vix, dtype="float64")
        self._min_dollar_volume = min_dollar_volume
        self._atr_pct_band = atr_pct_band

    def get_metrics(self, as_of: str) -> dict[str, SymbolMetrics]:
        return dict(self._metrics)

    def get_constituents(self, as_of: str) -> list[str]:
        return select_universe(
            self._metrics,
            as_of,
            min_dollar_volume=self._min_dollar_volume,
            atr_pct_band=self._atr_pct_band,
        )

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        if symbol not in self._frames:
            raise KeyError(f"데이터 없음: {symbol!r}")
        df = self._frames[symbol]
        if start is not None or end is not None:
            df = df.loc[start:end]
        return df

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series:
        vix = self._vix
        if start is not None or end is not None:
            vix = vix.loc[start:end]
        return vix


class CsvPointInTimeProvider:
    """로컬 CSV/Parquet 드롭인 provider (생존편향 없는 데이터 1순위 실경로 — 네트워크 없음).

    디렉토리 구조(예):
      <root>/metrics.csv         — symbol, listed_from, delisted_at, avg_dollar_volume, atr_pct, is_leveraged_or_inverse
      <root>/ohlcv/<SYMBOL>.csv  — date,open,high,low,close,volume (상폐종목 포함)
      <root>/vix.csv             — date,close
    벤더(Norgate/Sharadar 등)에서 export한 파일을 그대로 둔다. 실 데이터 로드는 여기에만.
    """

    def __init__(
        self,
        root: str,
        *,
        min_dollar_volume: float = 1e7,
        atr_pct_band: tuple[float, float] = (0.015, 0.05),
    ) -> None:
        self._root = root
        self._min_dollar_volume = min_dollar_volume
        self._atr_pct_band = atr_pct_band

    def _read(self, rel: str) -> pd.DataFrame:
        import os

        path = os.path.join(self._root, rel)
        if path.endswith(".parquet"):
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def get_metrics(self, as_of: str) -> dict[str, SymbolMetrics]:
        df = self._read("metrics.csv")
        out: dict[str, SymbolMetrics] = {}
        for row in df.to_dict("records"):
            delisted = row.get("delisted_at")
            out[str(row["symbol"])] = SymbolMetrics(
                listed_from=str(row["listed_from"]),
                delisted_at=None if pd.isna(delisted) else str(delisted),
                avg_dollar_volume=float(row["avg_dollar_volume"]),
                atr_pct=float(row["atr_pct"]),
                is_leveraged_or_inverse=bool(row["is_leveraged_or_inverse"]),
            )
        return out

    def get_constituents(self, as_of: str) -> list[str]:
        return select_universe(
            self.get_metrics(as_of),
            as_of,
            min_dollar_volume=self._min_dollar_volume,
            atr_pct_band=self._atr_pct_band,
        )

    def get_ohlcv(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        raw = self._read(f"ohlcv/{symbol}.csv")
        if "date" in raw.columns:
            raw = raw.set_index("date")
        df = normalize_ohlcv(raw)
        if start is not None or end is not None:
            df = df.loc[start:end]
        return df

    def get_vix(
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series:
        raw = self._read("vix.csv")
        if "date" in raw.columns:
            raw = raw.set_index("date")
        vix = pd.Series(raw["close"], dtype="float64").sort_index()
        if start is not None or end is not None:
            vix = vix.loc[start:end]
        return vix


class NorgateProvider:
    """Norgate Data SDK 기반 point-in-time provider (지연 import 스켈레톤).

    Norgate는 유료 구독 + 로컬 설치가 필요하다. norgatedata 패키지를 지연 import하며, 미설치/미구독 시
    명확한 안내 예외를 던진다(실호출 안 함). 실연동은 환경 구성 후 통합 phase에서 채운다.
    """

    survivorship_biased: bool = False  # Norgate는 상폐종목 포함 → 생존편향 없음

    def __init__(self, *, watchlist: str = "US Equities") -> None:
        self._watchlist = watchlist

    def _sdk(self):
        try:
            import norgatedata  # 지연 import: 미설치 환경에서 모듈 전체가 죽지 않게.
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise ImportError(
                "norgatedata 미설치/미구독. Norgate Data 구독+설치 후 사용하거나 "
                "CsvPointInTimeProvider로 export 파일을 꽂아라."
            ) from exc
        return norgatedata

    def get_metrics(self, as_of: str) -> dict[str, SymbolMetrics]:  # pragma: no cover
        self._sdk()
        raise NotImplementedError(
            "Norgate 실연동은 통합 phase에서 구현한다(구독·설치 필요). 현재는 스켈레톤."
        )

    def get_constituents(self, as_of: str) -> list[str]:  # pragma: no cover
        self._sdk()
        raise NotImplementedError("Norgate 실연동은 통합 phase에서 구현한다.")

    def get_ohlcv(  # pragma: no cover
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        self._sdk()
        raise NotImplementedError("Norgate 실연동은 통합 phase에서 구현한다.")

    def get_vix(  # pragma: no cover
        self, start: str | None = None, end: str | None = None
    ) -> pd.Series:
        self._sdk()
        raise NotImplementedError("Norgate 실연동은 통합 phase에서 구현한다.")
