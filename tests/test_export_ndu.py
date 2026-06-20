"""export_ndu CLI 테스트 (spec: specs/export_ndu.md).

모킹된 NDU provider만 사용 — 실 NDU/네트워크 불요. 산출 CSV가 norgate_bridge와 호환되는지,
fail-closed(미설치/실패/빈응답/data 밖) 동작을 검증. 실브로커/LLM/전략 무관.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_ndu  # noqa: E402

from agents.norgate_bridge import load_norgate_folder  # noqa: E402

_DATES = pd.date_range("2024-01-01", periods=20, freq="B")


class _MockNdu:
    """norgatedata.price_timeseries 흉내 — Date 인덱스 + OHLCV 컬럼 DataFrame 반환."""

    def __init__(self, *, fail=(), empty=()):
        self.fail = set(fail)
        self.empty = set(empty)

    def price_timeseries(self, symbol, **kwargs):
        if symbol in self.fail:
            raise RuntimeError(f"NDU 내부 오류({symbol})")
        if symbol in self.empty:
            return pd.DataFrame()
        n = len(_DATES)
        base = np.linspace(100, 120, n)
        df = pd.DataFrame(
            {"Open": base, "High": base * 1.01, "Low": base * 0.99,
             "Close": base, "Volume": np.full(n, 1_000_000.0)},
            index=pd.DatetimeIndex(_DATES, name="Date"),
        )
        return df


# --- export 성공 + 브리지 호환 ---


def test_export_writes_long_format_csv(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    paths = export_ndu.export_symbols(
        ["NVDA", "AAPL"], out, start=None, end=None, overwrite=False,
        provider=_MockNdu(),
    )
    assert len(paths) == 2
    df = pd.read_csv(out / "NVDA.csv")
    assert list(df.columns) == ["symbol", "date", "open", "high", "low", "close", "volume"]
    assert (df["symbol"] == "NVDA").all()
    assert df["date"].iloc[0] == "2024-01-01"


def test_exported_folder_loads_via_norgate_bridge(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    export_ndu.export_symbols(
        ["NVDA", "AAPL", "SPY"], out, start=None, end=None, overwrite=False,
        provider=_MockNdu(),
    )
    data = load_norgate_folder(out)
    assert set(data) == {"NVDA", "AAPL", "SPY"}
    assert list(data["NVDA"].columns) == ["open", "high", "low", "close", "volume"]


# --- overwrite ---


def test_existing_file_without_overwrite_fails(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=False, provider=_MockNdu())
    with pytest.raises(export_ndu.NduExportError):
        export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=False, provider=_MockNdu())


def test_overwrite_allows_replacing(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=False, provider=_MockNdu())
    paths = export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=True, provider=_MockNdu())
    assert paths[0].is_file()


# --- fail-closed ---


def test_sdk_unavailable_fails_clearly():
    def _broken_import(_name):
        raise ImportError("No module named 'norgatedata'")

    with pytest.raises(export_ndu.NduExportError) as exc:
        export_ndu._load_ndu_provider(import_fn=_broken_import)
    assert "norgatedata" in str(exc.value)


def test_symbol_export_failure_names_symbol(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    with pytest.raises(export_ndu.NduExportError) as exc:
        export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=False,
                                  provider=_MockNdu(fail=["NVDA"]))
    assert "NVDA" in str(exc.value)


def test_empty_response_fails(tmp_path):
    out = tmp_path / "data" / "ndu_export"
    with pytest.raises(export_ndu.NduExportError):
        export_ndu.export_symbols(["NVDA"], out, start=None, end=None, overwrite=False,
                                  provider=_MockNdu(empty=["NVDA"]))


def test_output_outside_data_is_rejected(tmp_path):
    # repo data/ 밖 경로 가드.
    assert export_ndu._is_under_data(export_ndu._ROOT / "data" / "x") is True
    assert export_ndu._is_under_data(tmp_path / "elsewhere") is False
