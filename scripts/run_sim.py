"""실데이터 시뮬 CLI — NDU/Norgate export CSV 폴더로 historical_sim을 돌려 성과를 출력/저장한다.

로직은 전부 기존 모듈 재사용(norgate_bridge → price_csv → historical_sim → perf_report). 이 파일은
인자 파싱 + 배선 + 출력만 하는 얇은 수동 CLI다.

사용:
  python scripts/run_sim.py --data-root data/survivorship_free --benchmark SPY --assume-no-events

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM 미연결.
이벤트 캘린더 실연동 없음. 전략 시그널 튜닝 없음. --data-root는 gitignore된 로컬 폴더여야 한다.

CRITICAL (fail-closed): 데이터 폴더 없음/CSV 없음/필수 컬럼 누락/컴퍼스(SPY)·벤치마크 심볼 없음/
거래일 0 → 추정하지 않고 명확한 메시지로 exit code 2.

spec: specs/run_sim.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 윈도우 콘솔(cp949)에서 리포트의 한글이 깨지지 않도록 stdout을 UTF-8로.
try:  # pragma: no cover - 환경 의존
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import pandas as pd

# scripts/를 직접 실행하든 패키지 루트에서 임포트하든 동작하도록 루트를 path에 보장.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.historical_sim import HistoricalResult, run_historical_simulation
from agents.norgate_bridge import DataAdapterError, load_norgate_folder
from agents.perf_report import format_performance_report
from agents.policy_loader import load_policy
from agents.price_csv import close_series
from agents.trade_diagnostics import compute_trade_diagnostics, format_trade_diagnostics
from algorithms.sizing import ShareMode

_COMPASS_SYMBOL = "SPY"   # 레짐/컴퍼스 고정 심볼.
_VIX_SYMBOL = "VIX"
_NEUTRAL_VIX = 15.0       # VIX 심볼 없을 때 중립 상수(레짐만, 거래 판단 아님).
_DEFAULT_CONFIG = _ROOT / "config"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="실데이터 시뮬 (NDU/Norgate export → 성과 리포트, 실주문 0)"
    )
    p.add_argument("--data-root", required=True, help="NDU/Norgate export CSV 폴더(로컬, gitignore)")
    p.add_argument("--symbols", nargs="*", default=None, help="거래 심볼(비면 보조 심볼 제외 전체)")
    p.add_argument("--start-date", default=None, help="거래일 시작 YYYY-MM-DD(비면 warmup 이후)")
    p.add_argument("--end-date", default=None, help="거래일 끝 YYYY-MM-DD")
    p.add_argument("--starting-cash", type=float, default=1000.0, help="시작 현금(기본 1000)")
    p.add_argument("--benchmark", default="SPY", help="벤치마크 심볼(기본 SPY)")
    p.add_argument("--warmup", type=int, default=200, help="start 미지정 시 건너뛸 초기 바 수")
    p.add_argument(
        "--share-mode", choices=("whole", "fractional"), default="whole",
        help="수량 단위: whole(기본 정수주) 또는 fractional(분수주 — 소액 계좌 고가주 시뮬)",
    )
    p.add_argument("--lot-size", type=float, default=0.001, help="분수주 최소단위(기본 0.001)")
    p.add_argument(
        "--assume-no-events", action="store_true",
        help="드라이런 편의: 이벤트 리스크 없음 가정(Mock). 기본 off면 이벤트 게이트 fail-closed. 실 캘린더 아님.",
    )
    p.add_argument("--output", default=None, help="성과 리포트 저장 경로(UTF-8). 콘솔에도 항상 출력.")
    return p


def _parse_date(value, label):
    if value is None:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        raise DataAdapterError(f"{label} 날짜 형식 오류: {value!r} (YYYY-MM-DD)")
    return ts


def _resolve_trading_days(index, start, end, warmup):
    """SPY 인덱스를 [start,end]로 자른다. start 비면 warmup 이후부터. 0개면 fail-closed."""
    idx = index
    if start is not None:
        idx = idx[idx >= start]
    else:
        idx = idx[warmup:]
    if end is not None:
        idx = idx[idx <= end]
    days = list(idx)
    if not days:
        raise DataAdapterError(
            "거래일 0개 — start/end 범위나 warmup을 확인하라(데이터 구간 밖일 수 있음)."
        )
    return days


def simulate(args) -> HistoricalResult:
    """데이터를 로드·배선해 historical_sim을 돌린다. 데이터 문제는 DataAdapterError(fail-closed)."""
    data = load_norgate_folder(args.data_root)   # 폴더/CSV/컬럼 문제 → DataAdapterError

    spy = close_series(data, _COMPASS_SYMBOL)            # 컴퍼스 SPY 필수
    benchmark_prices = close_series(data, args.benchmark)  # 벤치마크 필수

    if _VIX_SYMBOL in data:
        vix = close_series(data, _VIX_SYMBOL)
    else:
        vix = pd.Series(_NEUTRAL_VIX, index=spy.index)

    aux = {_COMPASS_SYMBOL, _VIX_SYMBOL, args.benchmark}
    if args.symbols:
        unknown = [s for s in args.symbols if s not in data]
        if unknown:
            raise DataAdapterError(f"폴더에 없는 심볼: {unknown} (있는 심볼: {sorted(data)})")
        universe = list(args.symbols)
    else:
        universe = [s for s in data if s not in aux]
    if not universe:
        raise DataAdapterError("거래 유니버스가 비었다 — --symbols 또는 폴더 구성을 확인하라.")

    price_data = {s: data[s] for s in universe}

    start = _parse_date(args.start_date, "--start-date")
    end = _parse_date(args.end_date, "--end-date")
    trading_days = _resolve_trading_days(spy.index, start, end, args.warmup)

    event_provider = MockEventRiskProvider(default=True) if args.assume_no_events else None
    share_mode = ShareMode(args.share_mode)

    return asyncio.run(run_historical_simulation(
        price_data=price_data,
        spy_prices=spy,
        vix=vix,
        policy=load_policy(_DEFAULT_CONFIG),
        account_cash=args.starting_cash,
        benchmark_prices=benchmark_prices,
        trading_days=trading_days,
        params=EvidenceParams(
            account_equity=args.starting_cash,
            share_mode=share_mode,
            lot_size=args.lot_size,
        ),
        event_provider=event_provider,
    ))


def _final_marks(args, result) -> dict[str, float]:
    """미청산 포지션의 미실현 pnl 진단용 마지막 종가(읽기 전용). 결측 심볼은 건너뜀(fail-closed)."""
    open_syms = list(result.portfolio.positions)
    if not open_syms:
        return {}
    try:
        data = load_norgate_folder(args.data_root)
    except DataAdapterError:
        return {}
    marks: dict[str, float] = {}
    for sym in open_syms:
        try:
            marks[sym] = float(close_series(data, sym).iloc[-1])
        except (DataAdapterError, IndexError, ValueError):
            continue  # 마크 결측 → 해당 포지션은 미실현 n/a(가짜 손익 금지).
    return marks


def run(args) -> int:
    """CLI 실행: 시뮬 → 리포트 출력/저장. 데이터 문제는 exit code 2(fail-closed)."""
    try:
        result = simulate(args)
    except DataAdapterError as exc:
        print(f"[데이터 오류] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    perf_text = format_performance_report(result.performance)
    diag = compute_trade_diagnostics(result.multiday, final_prices=_final_marks(args, result))
    diag_text = format_trade_diagnostics(diag)
    report_text = perf_text + "\n\n" + diag_text

    print(report_text)
    print(f"  (실주문 0 확인: {result.real_orders_placed} / {diag.real_orders_placed})")

    if args.output:
        out = Path(args.output)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report_text + "\n", encoding="utf-8")
        print(f"리포트 저장: {out}")
    return 0


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
