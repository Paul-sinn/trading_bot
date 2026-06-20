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

from agents.event_calendar import EventCalendarError, EventCalendarProvider
from agents.event_impact import (
    compare_runs,
    compute_event_impact,
    format_comparison,
    format_event_impact,
)
from agents.evidence import EvidenceParams, MockEventRiskProvider
from agents.feature_diagnostics import (
    compute_feature_diagnostics,
    format_feature_diagnostics,
)
from agents.feature_outcome import compute_feature_outcome, format_feature_outcome
from agents.feature_shadow_score import (
    compute_feature_shadow_score,
    format_feature_shadow_score,
)
from agents.shadow_bucket_analysis import (
    compute_shadow_bucket_analysis,
    format_shadow_bucket_analysis,
)
from agents.shadow_whatif import compute_shadow_whatif, format_shadow_whatif
from agents.robustness_report import (
    compute_robustness_report,
    format_robustness_report,
)
from agents.baseline_comparison import (
    compute_baseline_comparison,
    format_baseline_comparison,
)
from agents.order_plan import compute_order_plan_diagnostics, format_order_plan
from agents.historical_sim import HistoricalResult, run_historical_simulation
from agents.norgate_bridge import DataAdapterError, load_norgate_folder
from agents.perf_report import format_performance_report
from agents.policy_loader import load_policy
from agents.price_csv import close_series
from agents.sim_exit import ExitPolicy
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
    # 청산 옵션(기존 sim_exit 재사용). 아무 것도 안 주면 청산 미적용 — 포지션 OPEN 유지(기본 동작 불변).
    p.add_argument("--stop-loss-pct", type=float, default=None, help="진입가 대비 손절 비율(예: 0.10)")
    p.add_argument("--trailing-stop-pct", type=float, default=None, help="추적 고점 대비 청산 비율(예: 0.15)")
    p.add_argument("--max-holding-days", type=int, default=None, help="보유 일수 도달 시 시간청산")
    p.add_argument("--manual-exit-date", default=None, help="해당 날짜(YYYY-MM-DD)에 전량 청산")
    p.add_argument("--events-csv", default=None, help="이벤트 캘린더 CSV(date,event_type,ticker,severity,notes)")
    p.add_argument(
        "--assume-no-events", action="store_true",
        help="개발 바이패스 전용: 이벤트 리스크 없음 가정(Mock). 실 캘린더 아님. --events-csv가 우선.",
    )
    p.add_argument(
        "--compare-assume-no-events", action="store_true",
        help="(--events-csv 필요) bypass 런을 한 번 더 돌려 events-csv와 비교 출력(측정용 추가 실행).",
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


def _resolve_event_provider(args):
    """이벤트 provider 결정(fail-closed). --events-csv 우선, 없으면 --assume-no-events 바이패스.

    둘 다 없으면 이벤트 확인 불가 → DataAdapterError(exit 2). 라이브 API 미연결 — CSV/Mock만.
    """
    if args.events_csv:
        try:
            return EventCalendarProvider.from_csv(args.events_csv)
        except EventCalendarError as exc:
            raise DataAdapterError(f"이벤트 캘린더 오류: {exc}") from exc
    if args.assume_no_events:
        return MockEventRiskProvider(default=True)  # 개발 바이패스 전용.
    raise DataAdapterError(
        "이벤트 데이터 필요: --events-csv <경로> 또는 (개발 바이패스) --assume-no-events 를 지정하라."
    )


def _build_exit_policy(args) -> ExitPolicy | None:
    """청산 플래그 → ExitPolicy. 아무 것도 없으면 None(청산 미적용, 기존 동작 불변).

    잘못된 값(범위 밖 비율/0 이하 일수)은 ExitPolicy가 ValueError → DataAdapterError로 감싸 fail-closed.
    """
    if not any((
        args.stop_loss_pct is not None,
        args.trailing_stop_pct is not None,
        args.max_holding_days is not None,
        args.manual_exit_date is not None,
    )):
        return None
    try:
        return ExitPolicy(
            stop_loss_pct=args.stop_loss_pct,
            trail_pct=args.trailing_stop_pct,
            max_hold_days=args.max_holding_days,
            manual_exit_date=args.manual_exit_date,
        )
    except ValueError as exc:
        raise DataAdapterError(f"청산 설정 오류: {exc}") from exc


def simulate(args, *, event_provider=None) -> HistoricalResult:
    """데이터를 로드·배선해 historical_sim을 돌린다. 데이터 문제는 DataAdapterError(fail-closed).

    event_provider를 주면 그것을 쓰고, 없으면 args로 결정(_resolve_event_provider, fail-closed).
    """
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

    event_provider = event_provider or _resolve_event_provider(args)
    share_mode = ShareMode(args.share_mode)
    exit_policy = _build_exit_policy(args)

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
        exit_policy=exit_policy,
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


def _feature_inputs(args):
    """피처 진단용 (price_data, benchmark) 로드 — simulate와 동일 유니버스 규칙.

    리포트 전용이므로 데이터 문제는 전체 실행을 막지 않고 ({}, None)으로 fail-safe 처리한다.
    """
    try:
        data = load_norgate_folder(args.data_root)
        benchmark_prices = close_series(data, args.benchmark)
    except DataAdapterError:
        return {}, None
    aux = {_COMPASS_SYMBOL, _VIX_SYMBOL, args.benchmark}
    if args.symbols:
        universe = [s for s in args.symbols if s in data]
    else:
        universe = [s for s in data if s not in aux]
    return {s: data[s] for s in universe}, benchmark_prices


def run(args) -> int:
    """CLI 실행: 시뮬 → 리포트 출력/저장. 데이터 문제는 exit code 2(fail-closed)."""
    if args.compare_assume_no_events and not args.events_csv:
        print("[설정 오류] --compare-assume-no-events 는 --events-csv 와 함께 써야 한다.", file=sys.stderr)
        raise SystemExit(2)
    try:
        event_provider = _resolve_event_provider(args)   # fail-closed
        result = simulate(args, event_provider=event_provider)
    except DataAdapterError as exc:
        print(f"[데이터 오류] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    perf_text = format_performance_report(result.performance)
    diag = compute_trade_diagnostics(result.multiday, final_prices=_final_marks(args, result))
    sections = [perf_text, format_trade_diagnostics(diag)]

    # 피처 진단(측정 전용 — 매매/veto 불변, 판단 미사용). 데이터 로드 실패 시 섹션만 비운다.
    feat_price_data, feat_benchmark = _feature_inputs(args)
    feat_diag = compute_feature_diagnostics(
        result.multiday, feat_price_data, benchmark_prices=feat_benchmark
    )
    sections.append(format_feature_diagnostics(feat_diag))

    # 피처-성과 분석(승 vs 패 진입 피처 차이). 측정 전용 — 매매/veto 불변.
    outcome = compute_feature_outcome(diag, feat_diag)
    sections.append(format_feature_outcome(outcome))

    # 피처 섀도 스코어(랭킹 분리력 사후 평가). 측정 전용 — 매매에 미사용.
    shadow = compute_feature_shadow_score(diag, feat_diag)
    sections.append(format_feature_shadow_score(shadow))

    # 섀도 스코어 버킷 분석(고점수 버킷이 더 좋은 성과인지). 측정 전용 — 매매에 미사용.
    buckets = compute_shadow_bucket_analysis(diag, shadow)
    sections.append(format_shadow_bucket_analysis(buckets))

    # 섀도 필터 What-if(저점수 제거 시 성과 추정). 측정 전용 — 실 매매에 미적용.
    whatif = compute_shadow_whatif(diag, shadow)
    sections.append(format_shadow_whatif(whatif))

    # 강건성/안정성(심볼·기간 의존도). 측정 전용 — 기본은 트레이드 제거 근사(추가 재시뮬 없음).
    robustness = compute_robustness_report(result.multiday, feat_price_data, trade_diag=diag)
    sections.append(format_robustness_report(robustness))

    # 베이스라인 비교(SPY/QQQ/equal-weight/best-single 매수보유). 측정 전용 — 실 매매 미적용.
    try:
        full_data = load_norgate_folder(args.data_root)
        win = diag.equity_over_time
        b_start = win[0][0] if win else None
        b_end = win[-1][0] if win else None
        comparison = compute_baseline_comparison(
            result.performance, full_data, universe=list(feat_price_data),
            start=b_start, end=b_end, benchmark_symbol=args.benchmark,
        )
        sections.append(format_baseline_comparison(comparison))
    except DataAdapterError:
        pass  # 베이스라인 섹션만 생략(리포트 전용 — 전체 실행은 막지 않음).

    # 사전 주문계획(한정매수 + 진입 전 청산 첨부). 측정 전용 — can_trade_live=False, 실행 아님.
    order_plan = compute_order_plan_diagnostics(diag)
    sections.append(format_order_plan(order_plan))

    # events-csv 사용 시: 이벤트 영향 진단(차단된 후보). 측정 전용.
    if isinstance(event_provider, EventCalendarProvider):
        impact = compute_event_impact(result.multiday, event_provider=event_provider)
        sections.append(format_event_impact(impact))
        # 비교: bypass 런을 추가로 돌려(측정용) events-csv와 대조.
        if args.compare_assume_no_events:
            bypass = simulate(args, event_provider=MockEventRiskProvider(default=True))
            sections.append(format_comparison(compare_runs(bypass, result, event_provider=event_provider)))

    report_text = "\n\n".join(sections)
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
