"""run_sim CLI 테스트 (spec: specs/run_sim.md).

NDU/Norgate export CSV 폴더 → historical_sim 전체 파이프라인 → 성과 리포트. 데이터 없음/벤치마크
없음은 fail-closed(SystemExit 2). real orders=0. 전략 미변경. 네트워크/브로커 없음.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_sim  # noqa: E402

_DATES = pd.date_range("2025-01-01", periods=260, freq="B")
_LAST3_START = _DATES[-3].strftime("%Y-%m-%d")


def _ndu_csv(folder: Path, symbol: str, close_curve, volume=1_000_000.0):
    """NDU-style 심볼별 CSV(symbol 컬럼 없음, 대문자 컬럼) 작성."""
    close = np.asarray(close_curve, dtype=float)
    vol = np.full(len(close), volume)
    vol[-1] = volume * 5
    pd.DataFrame({
        "Date": _DATES.strftime("%Y-%m-%d"),
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": vol,
    }).to_csv(folder / f"{symbol}.csv", index=False)


def _ndu_folder(tmp_path: Path, *, with_bench=True) -> Path:
    d = tmp_path / "norgate"
    d.mkdir()
    _ndu_csv(d, "NVDA", np.linspace(80, 200, 260))
    _ndu_csv(d, "AAPL", np.linspace(90, 180, 260))
    _ndu_csv(d, "SPY", np.linspace(300, 400, 260))
    if with_bench:
        _ndu_csv(d, "BENCH", np.linspace(100, 110, 260))
    return d


def _args(parser, extra):
    return parser.parse_args(extra)


# --- 성공 경로 ---


def test_cli_dry_run_produces_report(tmp_path, capsys):
    d = _ndu_folder(tmp_path)
    parser = run_sim.build_arg_parser()
    args = _args(parser, [
        "--data-root", str(d),
        "--symbols", "NVDA", "AAPL",
        "--start-date", _LAST3_START,
        "--starting-cash", "1000000",
        "--benchmark", "BENCH",
        "--assume-no-events",
    ])
    code = run_sim.run(args)
    assert code == 0
    out = capsys.readouterr().out
    assert "Simulated Performance" in out
    assert "real_orders_placed : 0" in out


def test_cli_real_orders_zero_via_result(tmp_path):
    d = _ndu_folder(tmp_path)
    parser = run_sim.build_arg_parser()
    args = _args(parser, [
        "--data-root", str(d),
        "--start-date", _LAST3_START,
        "--starting-cash", "1000000",
        "--benchmark", "BENCH",
        "--assume-no-events",
    ])
    result = run_sim.simulate(args)          # HistoricalResult 직접
    assert result.real_orders_placed == 0
    assert len(result.performance.equity_curve) == 3
    assert result.performance.real_orders_placed == 0


def test_cli_writes_output_file(tmp_path):
    d = _ndu_folder(tmp_path)
    out_path = tmp_path / "report.txt"
    parser = run_sim.build_arg_parser()
    args = _args(parser, [
        "--data-root", str(d),
        "--start-date", _LAST3_START,
        "--starting-cash", "1000000",
        "--benchmark", "BENCH",
        "--output", str(out_path),
        "--assume-no-events",
    ])
    code = run_sim.run(args)
    assert code == 0
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")
    assert "Simulated Performance" in text


# --- fail-closed ---


def test_missing_data_folder_fails_safely(tmp_path):
    parser = run_sim.build_arg_parser()
    args = _args(parser, ["--data-root", str(tmp_path / "nope")])
    with pytest.raises(SystemExit) as exc:
        run_sim.run(args)
    assert exc.value.code == 2


def test_missing_benchmark_fails_safely(tmp_path):
    d = _ndu_folder(tmp_path, with_bench=False)   # BENCH 없음
    parser = run_sim.build_arg_parser()
    args = _args(parser, [
        "--data-root", str(d),
        "--start-date", _LAST3_START,
        "--benchmark", "BENCH",
        "--assume-no-events",
    ])
    with pytest.raises(SystemExit) as exc:
        run_sim.run(args)
    assert exc.value.code == 2


def test_missing_compass_spy_fails_safely(tmp_path):
    d = tmp_path / "nospy"
    d.mkdir()
    _ndu_csv(d, "NVDA", np.linspace(80, 200, 260))   # SPY 없음
    parser = run_sim.build_arg_parser()
    args = _args(parser, ["--data-root", str(d), "--assume-no-events"])
    with pytest.raises(SystemExit) as exc:
        run_sim.run(args)
    assert exc.value.code == 2


def test_unknown_symbol_fails_safely(tmp_path):
    d = _ndu_folder(tmp_path)
    parser = run_sim.build_arg_parser()
    args = _args(parser, [
        "--data-root", str(d),
        "--symbols", "ZZZZ",            # 폴더에 없음
        "--benchmark", "BENCH",
        "--assume-no-events",
    ])
    with pytest.raises(SystemExit) as exc:
        run_sim.run(args)
    assert exc.value.code == 2
