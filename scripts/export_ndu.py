"""NDU/Norgate SDK → gitignore된 CSV export CLI — run_sim이 먹는 데이터를 만든다.

선택 심볼의 일봉을 NDU SDK(norgatedata)로 받아 `data/` 아래 long-format CSV로 저장한다. SDK 접근(I/O)은
여기서만 하고, 산출 CSV는 기존 agents/norgate_bridge.py가 그대로 로드한다.

사용:
  python scripts/export_ndu.py --symbols SPY NVDA AAPL --start-date 2015-01-01 --overwrite

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. 전략 시그널 튜닝 없음. LLM/이벤트 캘린더 미연결.
로컬 CSV 데이터만 export. 시장 데이터는 커밋 금지 — 출력은 반드시 gitignore된 data/ 아래.

CRITICAL (fail-closed): NDU SDK 미설치/사용불가 → NduExportError. 심볼 실패/빈 응답/필수 컬럼 없음 →
심볼명 담은 NduExportError. 출력이 data/ 밖이면 거부.

spec: specs/export_ndu.md
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]

_OHLCV = ("open", "high", "low", "close", "volume")
_OUTPUT_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]
_DEFAULT_OUTPUT_DIR = "data/ndu_export"


class NduExportError(Exception):
    """NDU export 실패(SDK 미설치/심볼 실패/빈 응답/컬럼 누락/출력 위치 거부)."""


def _load_ndu_provider(import_fn=importlib.import_module):
    """norgatedata 모듈을 지연 import한다(테스트는 import_fn 주입). 실패 → NduExportError."""
    try:
        return import_fn("norgatedata")
    except ImportError as exc:
        raise NduExportError(
            "NDU SDK(norgatedata)를 불러올 수 없다 — `pip install norgatedata` 후 NDU(Norgate Data "
            "Updater) 앱이 실행 중인지 확인하라."
        ) from exc


def _to_long_format(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """NDU 응답(Date 인덱스/컬럼 + OHLCV)을 symbol,date,ohlcv long-format으로 변환한다."""
    out = df.reset_index()
    lowered = {str(c).strip().lower(): c for c in out.columns}

    missing = [c for c in _OHLCV if c not in lowered]
    if missing:
        raise NduExportError(f"{symbol}: NDU 응답에 필수 컬럼 없음 {missing} (있는 컬럼: {list(out.columns)})")

    date_key = lowered.get("date", out.columns[0])  # 인덱스명 Date 또는 첫 컬럼.
    result = pd.DataFrame({
        "symbol": symbol,
        "date": pd.to_datetime(out[date_key], errors="coerce").dt.strftime("%Y-%m-%d"),
    })
    for col in _OHLCV:
        result[col] = pd.to_numeric(out[lowered[col]], errors="coerce")
    result = result.dropna(subset=["date"])
    if len(result) == 0:
        raise NduExportError(f"{symbol}: 유효한 일봉 행이 없다")
    return result[_OUTPUT_COLS]


def fetch_symbol_frame(symbol: str, start, end, *, provider) -> pd.DataFrame:
    """provider로 심볼 일봉을 받아 long-format으로 돌려준다. 실패/빈 응답 → NduExportError."""
    kwargs = {"format": "pandas-dataframe", "timeseriesformat": "pandas-dataframe"}
    if start is not None:
        kwargs["start_date"] = start
    if end is not None:
        kwargs["end_date"] = end
    try:
        df = provider.price_timeseries(symbol, **kwargs)
    except NduExportError:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK 예외를 명확히 래핑
        raise NduExportError(f"{symbol} export 실패: {type(exc).__name__}: {exc}") from exc

    if df is None or len(df) == 0:
        raise NduExportError(f"{symbol}: NDU가 데이터를 반환하지 않음(상장 전/심볼 오류/구독 범위 밖?)")
    return _to_long_format(df, symbol)


def export_symbols(symbols, output_dir, *, start=None, end=None, overwrite=False, provider=None) -> list[Path]:
    """심볼별 CSV를 output_dir에 저장한다. provider 없으면 NDU SDK 로드. 파일별 검증(fail-closed)."""
    if provider is None:
        provider = _load_ndu_provider()

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for symbol in symbols:
        path = out_dir / f"{symbol}.csv"
        if path.exists() and not overwrite:
            raise NduExportError(f"{path} 이미 존재 — 덮어쓰려면 --overwrite")
        frame = fetch_symbol_frame(symbol, start, end, provider=provider)
        frame.to_csv(path, index=False)
        written.append(path)
    return written


def _is_under_data(path) -> bool:
    """path가 repo data/ 아래인지(시장 데이터 커밋 방지 가드)."""
    data_root = (_ROOT / "data").resolve()
    try:
        Path(path).resolve().relative_to(data_root)
        return True
    except ValueError:
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="NDU/Norgate SDK → gitignore된 CSV export (run_sim 입력용)"
    )
    p.add_argument("--symbols", nargs="+", required=True, help="export할 심볼 (예: SPY NVDA AAPL)")
    p.add_argument("--output-dir", default=_DEFAULT_OUTPUT_DIR, help="출력 폴더(반드시 data/ 아래)")
    p.add_argument("--start-date", default=None, help="시작 YYYY-MM-DD(없으면 SDK 기본)")
    p.add_argument("--end-date", default=None, help="끝 YYYY-MM-DD(없으면 SDK 기본)")
    p.add_argument("--overwrite", action="store_true", help="기존 파일 덮어쓰기")
    return p


def run(args) -> int:
    """CLI 실행: data/ 가드 → export → 저장 경로 출력. 실패는 exit code 2(fail-closed)."""
    if not _is_under_data(args.output_dir):
        print(
            f"[거부] --output-dir 은 gitignore된 data/ 아래여야 한다(시장 데이터 커밋 방지): {args.output_dir}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        paths = export_symbols(
            args.symbols, args.output_dir,
            start=args.start_date, end=args.end_date, overwrite=args.overwrite,
        )
    except NduExportError as exc:
        print(f"[export 오류] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    for p in paths:
        print(f"export: {p}")
    print(f"완료: {len(paths)}개 심볼 → {args.output_dir} (커밋 금지 — gitignore)")
    return 0


def main(argv=None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
