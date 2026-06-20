"""Norgate-export / CSV → historical_sim price_data 어댑터 (얇은 변환).

long-format 가격 데이터(symbol,date,open,high,low,close,volume)를 dict[symbol, OHLCV DataFrame]로
바꾼다. 기존 agents.data_adapter.normalize_ohlcv를 재사용한다. 파일/프레임 입력형 — NDU 라이브 연결 아님.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0(이 모듈은 데이터만).
LLM/이벤트 캘린더 실연동 없음. 전략 시그널 변경 없음.

CRITICAL (fail-closed, 가정 금지): 필수 컬럼 누락 → DataAdapterError(추정/기본값 없음). 가격/날짜
결측·무효 행 → 드롭(가짜 데이터 안 만듦). 유효 행 0 심볼 → 제외.

spec: specs/price_csv.md
"""

from __future__ import annotations

import pandas as pd

from agents.data_adapter import normalize_ohlcv

_REQUIRED = ("symbol", "date", "open", "high", "low", "close", "volume")
_OHLCV = ("open", "high", "low", "close", "volume")


class DataAdapterError(Exception):
    """CSV/프레임 입력이 필수 컬럼을 빠뜨렸거나 로드에 실패했을 때(fail-closed)."""


def load_price_data_from_frame(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """long-format DataFrame을 dict[symbol, OHLCV]로 변환한다(검증 + fail-closed)."""
    lowered = {str(c).strip().lower(): c for c in df.columns}
    missing = [c for c in _REQUIRED if c not in lowered]
    if missing:
        raise DataAdapterError(
            f"필수 컬럼 누락: {missing} (있는 컬럼: {list(df.columns)}) — 컬럼 추정 안 함"
        )

    work = df.rename(columns={lowered[c]: c for c in _REQUIRED}).copy()
    # 날짜 파싱(무효 → NaT → 드롭). 숫자 강제(무효 → NaN → normalize_ohlcv dropna).
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    for col in _OHLCV:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["date", "symbol"])

    out: dict[str, pd.DataFrame] = {}
    for symbol, group in work.groupby("symbol"):
        frame = group.set_index("date").sort_index()
        norm = normalize_ohlcv(frame[list(_OHLCV)])  # 컬럼 매핑·dropna·정렬·중복제거 재사용.
        if len(norm) > 0:
            out[str(symbol)] = norm
    return out


def load_price_data_from_csv(path) -> dict[str, pd.DataFrame]:
    """CSV 파일을 읽어 dict[symbol, OHLCV]로 변환한다. 파일 없음/파싱 실패 → DataAdapterError."""
    try:
        df = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise DataAdapterError(f"CSV 파일 없음: {path}") from exc
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise DataAdapterError(f"CSV 읽기 실패: {path} ({exc})") from exc
    return load_price_data_from_frame(df)


def close_series(price_data: dict[str, pd.DataFrame], symbol: str) -> pd.Series:
    """심볼의 close 시리즈를 돌려준다(컴퍼스/벤치마크용). 없으면 DataAdapterError."""
    df = price_data.get(symbol)
    if df is None:
        raise DataAdapterError(f"심볼 없음: {symbol!r}")
    return df["close"]
