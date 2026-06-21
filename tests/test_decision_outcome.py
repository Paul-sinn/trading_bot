"""decision_outcome 테스트 (spec: specs/decision_outcome.md).

결정 결과 채점/전진 검증(실험 전용). 로그/시뮬/OHLCV만 읽어 사후 채점 — 스캐너/디시전/RiskGate/베이스라인
미변경. forward만 사용. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from agents.decision import Decision
from agents.decision_outcome import (
    ForwardOutcome,
    ReentryContext,
    ScoredRecord,
    build_outcome_report,
    compute_forward_outcome,
    compute_reentry_context,
    format_outcome_markdown,
    score_records,
    scored_to_jsonl,
    summarize_buys,
    summarize_rejects,
)
from agents.trade_diagnostics import TradeLeg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.decision_outcome_score as dos  # noqa: E402


def _leg(symbol, entry, exit, reason="time_stop"):
    return TradeLeg(symbol=symbol, entry_date=entry, exit_date=exit, entry_price=100.0,
                    exit_price=110.0, qty=1.0, pnl=10.0, pnl_pct=0.1, exit_reason=reason)


# --- forward 결과 ---


def test_compute_forward_outcome_returns_mfe_mae_stops():
    closes = [100, 105, 110, 95, 120]
    highs = [100, 106, 112, 96, 121]
    lows = [100, 104, 108, 84, 119]
    o = compute_forward_outcome(closes, highs, lows, horizons=(1, 2, 4), stop=0.15, trail=0.20, max_hold=60)
    assert o.scorable is True and o.ref_price == 100.0 and o.forward_bars == 4
    assert abs(o.returns[1] - 0.05) < 1e-9
    assert abs(o.returns[4] - 0.20) < 1e-9
    assert abs(o.mfe - 0.21) < 1e-9            # 고가 121
    assert abs(o.mae - (-0.16)) < 1e-9          # 저가 84
    assert o.stop_hit is True                   # 84 <= 85
    assert o.trail_hit is True                  # 피크 112 대비 84 = -25%
    assert o.time_close is False                # stop/trail 발동


def test_compute_forward_outcome_unscorable_cases():
    assert compute_forward_outcome([], [], []).scorable is False
    only = compute_forward_outcome([100], [100], [100])
    assert only.scorable is False and only.reason == "forward 바 없음"


def test_forward_outcome_partial_horizon_marked_none():
    o = compute_forward_outcome([100, 101, 102], [100, 101, 102], [100, 101, 102],
                                horizons=(1, 5, 60), max_hold=60)
    assert o.returns[1] is not None and o.returns[5] is None and o.returns[60] is None
    assert o.time_close is None                 # 60바 미달 + 미발동


# --- 재진입 ---


def test_reentry_context_populated():
    legs = [_leg("MU", "2025-01-02", "2025-02-02", "time_stop"),
            _leg("MU", "2025-03-01", None)]
    ctx = compute_reentry_context("MU", "2025-04-01", legs)
    assert ctx.is_reentry is True and ctx.available is True
    assert ctx.previous_exit_date == "2025-02-02" and ctx.previous_exit_reason == "time_stop"
    assert ctx.same_symbol_reentry_count == 2
    assert ctx.days_since_last_exit == 58


def test_reentry_first_time_and_unavailable():
    legs = [_leg("MU", "2025-03-01", None)]
    assert compute_reentry_context("MU", "2025-01-01", legs).is_reentry is False
    un = compute_reentry_context("MU", "2025-01-01", None)
    assert un.available is False and un.is_reentry is None


# --- score_records (df) ---


def _df(start="2025-01-02", n=70, base=100.0):
    idx = pd.bdate_range(start=start, periods=n)
    closes = [base + i for i in range(n)]
    return pd.DataFrame({"open": closes, "high": [c + 1 for c in closes],
                         "low": [c - 1 for c in closes], "close": closes, "volume": [1e6] * n}, index=idx)


def test_score_records_scorable_and_unscorable():
    price_data = {"MU": _df()}
    records = [
        {"date": "2025-01-02", "symbol": "MU", "decision": "BUY", "reason": "ok"},
        {"date": "2025-01-03", "symbol": "GHOST", "decision": "BUY", "reason": "ok"},   # 데이터 없음
        {"date": "2030-01-01", "symbol": "MU", "decision": "REJECT", "reason": "x"},    # 거래일 아님
    ]
    scored = score_records(records, price_data, legs=[])
    by = {(s.symbol, s.date): s for s in scored}
    assert by[("MU", "2025-01-02")].outcome.scorable is True
    assert by[("GHOST", "2025-01-03")].outcome.scorable is False
    assert by[("MU", "2030-01-01")].outcome.scorable is False    # 크래시 없이 unscorable


def test_score_records_from_to_date_filter():
    price_data = {"MU": _df()}
    recs = [{"date": "2025-01-02", "symbol": "MU", "decision": "BUY", "reason": ""},
            {"date": "2025-01-10", "symbol": "MU", "decision": "BUY", "reason": ""}]
    scored = score_records(recs, price_data, legs=[], from_date="2025-01-05")
    assert [s.date for s in scored] == ["2025-01-10"]


# --- 집계 / 분리 채점 ---


def _scored(symbol, decision, ret60, *, reason="r"):
    o = ForwardOutcome(True, None, 100.0, {1: 0.01, 5: 0.02, 10: 0.03, 20: 0.04, 60: ret60},
                       0.3, -0.1, False, False, True, 60)
    re = ReentryContext(False, None, None, None, 0, available=True)
    return ScoredRecord(date="2025-06-01", symbol=symbol, decision=decision, reason=reason, outcome=o, reentry=re)


def test_summaries_separate_buy_reject_skip():
    scored = [_scored("MU", "BUY", 0.5), _scored("AMD", "BUY", -0.1),
              _scored("INTC", "REJECT", 0.3, reason="weight 제안: rejected"),   # 놓친 승자
              _scored("WDC", "REJECT", -0.2, reason="VETO: cap"),               # 옳은 거절
              _scored("AAPL", "SKIP", 0.0)]
    b = summarize_buys(scored)
    r = summarize_rejects(scored)
    assert b.n == 2 and b.scorable == 2
    assert b.hit_rate[60] == 0.5                  # MU>0, AMD<0
    assert any(s == "INTC" for s, _, _ in r.missed_winners)
    assert any(s == "WDC" for s, _, _ in r.good_rejects)
    rep = build_outcome_report(scored)
    assert rep.skip_count == 1 and rep.real_orders_placed == 0


def test_format_markdown_answers_and_jsonl():
    scored = [_scored("MU", "BUY", 0.5), _scored("AMD", "REJECT", 0.3, reason="VETO: x")]
    rep = build_outcome_report(scored)
    md = format_outcome_markdown(rep)
    assert "Decision Outcome Score" in md
    assert "BUY가 양의 forward return" in md
    assert "unscorable" in md and "real_orders_placed = 0" in md
    line = json.loads(scored_to_jsonl(scored).splitlines()[0])
    assert line["real_orders_placed"] == 0 and "outcome" in line and "reentry" in line


# --- 상수/기본값 잠금 ---


def test_locked_constants_and_run_sim_defaults():
    assert dos._FILL_MODEL == "next-bar-limit" and dos._BUFFER == 0.03
    assert dos._STOP == 0.15 and dos._TRAIL == 0.20 and dos._MAX_HOLD == 60
    args = dos.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


# --- 러너: backfill / 출력 파일 / 브로커 미사용 ---


def test_runner_backfill_writes_files(monkeypatch, tmp_path):
    df = _df()
    report = SimpleNamespace(report_date="2025-01-02", decisions=(
        SimpleNamespace(symbol="MU", effective_decision=Decision.BUY,
                        veto=SimpleNamespace(passed=True, reasons=()), rationale="weight ok"),
        SimpleNamespace(symbol="AMD", effective_decision=Decision.HOLD,
                        veto=SimpleNamespace(passed=False, reasons=("VETO: cap",)), rationale="rej"),
    ))
    multiday = SimpleNamespace(day_results=(SimpleNamespace(report=report),))

    monkeypatch.setattr(dos.run_sim, "load_norgate_folder", lambda root: {"MU": df, "AMD": df})
    monkeypatch.setattr(dos, "compute_trade_diagnostics", lambda md: SimpleNamespace(trades=()))
    captured = {}

    def _fake(args):
        captured["args"] = args
        return SimpleNamespace(multiday=multiday, real_orders_placed=0)

    monkeypatch.setattr(dos.run_sim, "simulate", _fake)
    report_obj, scored, error = dos.run_decision_outcome(
        data_root="x", backfill=True, events_csv=None, assume_no_events=True, simulate_fn=_fake)
    assert error is None
    a = captured["args"]
    assert a.entry_fill_model == "next-bar-limit" and a.max_holding_days == 60 and tuple(a.weekend_exit_symbols) == ()
    decisions = {s.symbol: s.decision for s in scored}
    assert decisions["MU"] == "BUY" and decisions["AMD"] == "REJECT"
    assert report_obj.real_orders_placed == 0

    md_path = tmp_path / "o.md"
    jsonl_path = tmp_path / "o.jsonl"
    rc = dos.run(SimpleNamespace(
        data_root="x", benchmark="SPY", jsonl="none", backfill=True, from_date=None, to_date=None,
        starting_cash=1000.0, events_csv="x", assume_no_events=True,
        output_md=str(md_path), output_jsonl=str(jsonl_path)))
    assert rc == 0 and md_path.exists() and jsonl_path.exists()
    assert all(json.loads(l)["real_orders_placed"] == 0
               for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip())


def test_runner_missing_jsonl_without_backfill_errors(monkeypatch):
    monkeypatch.setattr(dos.run_sim, "load_norgate_folder", lambda root: {"MU": _df()})
    monkeypatch.setattr(dos, "compute_trade_diagnostics", lambda md: SimpleNamespace(trades=()))
    monkeypatch.setattr(dos.run_sim, "simulate",
                        lambda args: SimpleNamespace(multiday=SimpleNamespace(day_results=()), real_orders_placed=0))
    report, scored, error = dos.run_decision_outcome(
        data_root="x", backfill=False, jsonl_path="does_not_exist.jsonl",
        events_csv=None, assume_no_events=True)
    assert report is None and "JSONL 없음" in error
