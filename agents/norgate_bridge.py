"""NDU/Norgate export → historical_sim 브리지 (얇은 변환).

NDU/Norgate가 export한 가격 CSV(심볼별 파일 폴더 또는 단일 파일)를 dict[symbol, OHLCV]로 바꾼다.
검증/정규화는 agents.price_csv.load_price_data_from_frame를 재사용한다. NDU 라이브 SDK 연결 아님 —
export 파일 입력형(NDU 켜둘 필요 없음).

NDU 심볼별 CSV는 보통 symbol 컬럼이 없고 파일명이 심볼(NVDA.csv) → 파일명 stem을 symbol로 주입한 뒤
price_csv로 넘긴다. symbol 컬럼이 있는 long-format 파일은 그대로 처리.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0(이 모듈은 데이터만).
LLM/이벤트 캘린더 실연동 없음. 전략 시그널 변경 없음.

CRITICAL (fail-closed, 가정 금지): 필수 컬럼이 없으면 파일명을 담은 DataAdapterError. 결측/무효 값 행은
price_csv가 드롭(가짜 데이터 안 만듦).

spec: specs/norgate_bridge.md
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from agents.price_csv import DataAdapterError, load_price_data_from_frame

__all__ = ["DataAdapterError", "load_norgate_csv", "load_norgate_folder"]


def _read_with_symbol(path: Path) -> pd.DataFrame:
    """심볼별 CSV를 읽어, symbol 컬럼이 없으면 파일명 stem을 symbol로 주입한다."""
    try:
        df = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise DataAdapterError(f"파일 없음: {path}") from exc
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise DataAdapterError(f"CSV 읽기 실패: {path.name} ({exc})") from exc

    cols = {str(c).strip().lower() for c in df.columns}
    if "symbol" not in cols:
        df = df.copy()
        df["symbol"] = path.stem  # 파일명 = 심볼(NDU 심볼별 export).
    return df


def load_norgate_csv(path) -> dict[str, pd.DataFrame]:
    """NDU export 단일 파일을 dict[symbol, OHLCV]로 로드한다."""
    p = Path(path)
    if not p.is_file():
        raise DataAdapterError(f"파일 없음: {path}")
    try:
        return load_price_data_from_frame(_read_with_symbol(p))
    except DataAdapterError as exc:
        raise DataAdapterError(f"{p.name}: {exc}") from exc


def load_norgate_folder(folder) -> dict[str, pd.DataFrame]:
    """NDU export 폴더의 모든 *.csv를 로드해 병합한다(파일별 검증, 파일명 담은 에러)."""
    d = Path(folder)
    if not d.is_dir():
        raise DataAdapterError(f"폴더 없음: {folder}")
    files = sorted(d.glob("*.csv"))
    if not files:
        raise DataAdapterError(f"CSV 파일 없음: {folder}")

    out: dict[str, pd.DataFrame] = {}
    for f in files:
        try:
            out.update(load_price_data_from_frame(_read_with_symbol(f)))
        except DataAdapterError as exc:
            raise DataAdapterError(f"{f.name}: {exc}") from exc
    if not out:
        raise DataAdapterError(f"유효한 가격 데이터 없음: {folder}")
    return out
