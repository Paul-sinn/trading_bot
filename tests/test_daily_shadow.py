"""daily_shadow 테스트 (spec: specs/daily_shadow.md).

일간 섀도 리포트/전진 원장(실험 전용). 결정 누적(멱등) + 성숙 결과 채점(미성숙 pending). 스캐너/디시전/
RiskGate/베이스라인 미변경. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from agents.daily_shadow import (
    build_daily_shadow,
    count_matured,
    count_newly_matured,
    count_pending,
    format_daily_shadow_markdown,
    merge_decision_ledger,
    record_id,
    upsert_outcome_ledger,
)
from agents.decision_log import make_record
from agents.decision_outcome import (
    ForwardOutcome,
    ReentryContext,
    ScoredRecord,
    summarize_buys,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.daily_shadow_report as dsr  # noqa: E402


def _ret_dict(vals):
    base = {1: None, 5: None, 10: None, 20: None, 60: None}
    base.update(vals)
    return base


def _scored(symbol, decision, returns, *, reentry=True):
    o = ForwardOutcome(True, None, 100.0, returns, 0.3, -0.1, False, False, None, 5)
    re = ReentryContext(reentry, "time_stop" if reentry else None, 10 if reentry else None,
                        "2025-01-01" if reentry else None, 1 if reentry else 0, available=True)
    return ScoredRecord(date="2025-06-01", symbol=symbol, decision=decision, reason="r", outcome=o, reentry=re)


# --- record_id / 원장 멱등 ---


def test_record_id_and_merge_dedupe():
    existing = [{"date": "2026-06-18", "symbol": "NVDA", "decision": "BUY"}]
    new = [{"date": "2026-06-18", "symbol": "NVDA", "decision": "BUY"},   # 중복
           {"date": "2026-06-18", "symbol": "MU", "decision": "REJECT"}]
    merged, added = merge_decision_ledger(existing, new)
    assert added == 1 and len(merged) == 2
    assert record_id(merged[-1]) == "2026-06-18|MU|REJECT"
    # 재실행: 같은 new를 다시 → 추가 0(중복 없음).
    merged2, added2 = merge_decision_ledger(merged, new)
    assert added2 == 0 and len(merged2) == 2


def test_upsert_outcome_ledger_updates_by_id():
    existing = [{"date": "d", "symbol": "MU", "decision": "BUY", "outcome": {"returns": {"60": None}}}]
    new = [{"date": "d", "symbol": "MU", "decision": "BUY", "outcome": {"returns": {"60": 0.5}}}]
    merged = upsert_outcome_ledger(existing, new)
    assert len(merged) == 1                                  # upsert(중복 아님)
    assert merged[0]["outcome"]["returns"]["60"] == 0.5      # 최신으로 갱신


# --- pending / matured / newly ---


def test_count_matured_and_pending():
    scored = [_scored("MU", "BUY", _ret_dict({1: 0.01, 5: 0.02})),   # 10/20/60 pending
              _scored("AMD", "BUY", _ret_dict({1: 0.0, 5: 0.01, 60: 0.3}))]
    matured = count_matured(scored, decision="BUY")
    pending = count_pending(scored, decision="BUY")
    assert matured[1] == 2 and matured[5] == 2 and matured[60] == 1
    assert pending[60] == 1 and pending[10] == 2


def test_count_newly_matured_vs_existing():
    scored = [_scored("MU", "BUY", _ret_dict({1: 0.01, 5: 0.02, 60: 0.3}))]
    existing_by_id = {"2025-06-01|MU|BUY": {"outcome": {"returns": {"1": 0.01, "5": None, "60": None}}}}
    newly = count_newly_matured(existing_by_id, scored)
    assert newly[1] == 0          # 이미 성숙
    assert newly[5] == 1 and newly[60] == 1   # 이번에 새로 성숙


# --- build / format ---


def test_build_and_format_daily_shadow():
    scored = [_scored("MU", "BUY", _ret_dict({1: 0.01, 5: 0.02, 60: 0.5})),
              _scored("AMD", "BUY", _ret_dict({1: 0.0, 60: -0.1}))]
    today = [make_record("2026-06-18", "NVDA", "BUY", reason="ok", riskgate_passed=True, position_shares=1.0),
             make_record("2026-06-18", "MU", "REJECT", reason="weight 제안: rejected", riskgate_passed=False,
                         riskgate_reasons=("VETO: cap",)),
             make_record("2026-06-18", "AAPL", "SKIP", reason="스캐너 후보 미선정")]
    rep = build_daily_shadow("2026-06-18", today, scored, {}, buy_summary=summarize_buys(scored))
    assert (rep.n_buy, rep.n_reject, rep.n_skip) == (1, 1, 1)
    assert rep.riskgate_vetoes == 1
    assert rep.matured_counts[60] == 2 and rep.pending_counts[10] == 2
    assert rep.newly_matured[60] == 2          # existing 비었음 → 전부 newly
    assert rep.reentry_total == 2 and rep.reentry_count == 2
    assert rep.real_orders_placed == 0
    md = format_daily_shadow_markdown(rep)
    assert "Daily Shadow Report — 2026-06-18" in md
    assert "오늘 BUY" in md and "NVDA" in md
    assert "결과 성숙" in md and "pending" in md
    assert "real_orders_placed = 0" in md


# --- 상수/기본값 잠금 ---


def test_locked_constants_and_run_sim_defaults():
    assert dsr._FILL_MODEL == "next-bar-limit" and dsr._BUFFER == 0.03
    assert dsr._STOP == 0.15 and dsr._TRAIL == 0.20 and dsr._MAX_HOLD == 60 and dsr._SHARE_MODE == "fractional"
    args = dsr.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


# --- 러너: 멱등 append / pending / 브로커 미사용 ---


def _df(periods=66, base=100.0):
    idx = pd.bdate_range(start="2025-01-02", periods=periods)
    closes = [base + i for i in range(periods)]
    return pd.DataFrame({"open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
                         "close": closes, "volume": [1e6] * periods}, index=idx)


def test_runner_idempotent_append_and_pending(monkeypatch, tmp_path):
    df = _df(periods=66)
    rep_date = str(df.index[60].date())          # 5 forward 바 → 1d/5d matured, 60d pending
    today = [make_record(rep_date, "MU", "BUY", reason="ok", riskgate_passed=True, position_shares=1.0)]

    monkeypatch.setattr(dsr, "build_decision_records",
                        lambda *, settings, end=None, simulate_fn=None: (rep_date, today, None))
    monkeypatch.setattr(dsr.run_sim, "load_norgate_folder", lambda root: {"MU": df})
    monkeypatch.setattr(dsr, "compute_trade_diagnostics", lambda md: SimpleNamespace(trades=()))
    monkeypatch.setattr(dsr, "run_shadow_health",
                        lambda **k: (SimpleNamespace(status="PASS", findings=()), None))
    captured = {}

    def _fake(args):
        captured["args"] = args
        return SimpleNamespace(multiday=SimpleNamespace(day_results=()), real_orders_placed=0)

    monkeypatch.setattr(dsr.run_sim, "simulate", _fake)

    dec_path = tmp_path / "dec.jsonl"
    out_path = tmp_path / "out.jsonl"
    md_path = tmp_path / "daily.md"
    kw = dict(data_root="x", events_csv=None, assume_no_events=True,
              decision_ledger=str(dec_path), outcome_ledger=str(out_path), daily_md=str(md_path))

    report, stats, error = dsr.run_daily_shadow(**kw)
    assert error is None
    # 진입 잠금 확인(leg 시뮬).
    assert captured["args"].entry_fill_model == "next-bar-limit" and captured["args"].max_holding_days == 60
    assert stats["added"] == 1
    assert report.matured_counts[1] == 1 and report.matured_counts[5] == 1
    assert report.pending_counts[60] == 1          # 60d 미성숙 → pending(실패 아님)
    assert report.real_orders_placed == 0
    assert md_path.exists() and "real_orders_placed = 0" in md_path.read_text(encoding="utf-8")
    dec_lines_1 = [l for l in dec_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(dec_lines_1) == 1

    # 재실행: 중복 append 없음.
    report2, stats2, _ = dsr.run_daily_shadow(**kw)
    assert stats2["added"] == 0
    dec_lines_2 = [l for l in dec_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(dec_lines_2) == 1                    # 여전히 1줄(멱등)
    assert all(json.loads(l).get("real_orders_placed", 0) == 0 for l in dec_lines_2)
