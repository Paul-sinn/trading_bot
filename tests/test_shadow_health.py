"""shadow_health 테스트 (spec: specs/shadow_health.md).

섀도 런 헬스 체크/데이터 신선도 가드(실험 전용). 데이터·원장 검증만 — 스캐너/디시전/RiskGate/베이스라인
미변경. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from agents.shadow_health import (
    FAIL,
    PASS,
    WARN,
    build_health,
    format_health_markdown,
    health_to_json,
    worst_status,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.shadow_health_check as shc  # noqa: E402


def _dec(date, symbol, decision, **kw):
    return {"date": date, "symbol": symbol, "decision": decision, "real_orders_placed": 0, **kw}


# --- 상태 종합 ---


def test_worst_status():
    assert worst_status([PASS, WARN, PASS]) == WARN
    assert worst_status([PASS, WARN, FAIL]) == FAIL
    assert worst_status([]) == PASS


# --- 개별 탐지 ---


def test_detects_missing_symbols():
    rep = build_health(universe=("MU", "NVDA", "INTC"), available_symbols=("MU", "NVDA"),
                       last_dates={"MU": "2026-06-18", "NVDA": "2026-06-18"})
    assert rep.n_missing == 1
    assert any(f.check == "missing_symbols" and f.status == WARN for f in rep.findings)
    assert rep.status == WARN


def test_detects_stale_data():
    rep = build_health(universe=("MU", "NVDA"), available_symbols=("MU", "NVDA"),
                       last_dates={"MU": "2026-06-18", "NVDA": "2026-05-01"}, stale_days=5)
    assert rep.n_stale == 1
    assert any(f.check == "stale_symbols" for f in rep.findings)
    assert rep.status == WARN


def test_detects_non_trading_day_report_date():
    rep = build_health(universe=("MU",), available_symbols=("MU",), last_dates={"MU": "2026-06-18"},
                       report_date="2026-06-20", trading_days={"2026-06-18", "2026-06-17"})
    assert any(f.check == "report_date" and f.status == WARN for f in rep.findings)


def test_detects_duplicate_decisions():
    recs = [_dec("2026-06-18", "MU", "BUY"), _dec("2026-06-18", "MU", "BUY")]
    rep = build_health(decision_records=recs)
    assert rep.dup_decisions == 1
    assert any(f.check == "duplicate_decisions" for f in rep.findings)


def test_detects_malformed_is_fail():
    rep = build_health(decision_records=[_dec("d", "MU", "BUY")], decision_malformed=2)
    assert rep.malformed == 2
    assert rep.status == FAIL
    assert any(f.check == "malformed_jsonl" and f.status == FAIL for f in rep.findings)


def test_detects_real_orders_nonzero_is_fail():
    bad = {"date": "d", "symbol": "MU", "decision": "BUY", "real_orders_placed": 3}
    rep = build_health(decision_records=[bad])
    assert rep.status == FAIL
    assert any(f.check == "real_orders" and f.status == FAIL for f in rep.findings)


def test_missing_required_fields_warn():
    rep = build_health(decision_records=[{"date": "d", "symbol": "MU"}])   # decision 누락
    assert any(f.check == "missing_fields" for f in rep.findings)


def test_pass_when_clean():
    rep = build_health(universe=("MU",), available_symbols=("MU",), last_dates={"MU": "2026-06-18"},
                       report_date="2026-06-18", trading_days={"2026-06-18"},
                       decision_records=[_dec("2026-06-18", "MU", "BUY")],
                       outcome_records=[{"date": "2026-06-18", "symbol": "MU", "decision": "BUY",
                                         "outcome": {}, "real_orders_placed": 0}])
    assert rep.status == PASS and rep.real_orders_placed == 0


# --- json / markdown ---


def test_health_json_and_markdown():
    rep = build_health(universe=("MU", "INTC"), available_symbols=("MU",), last_dates={"MU": "2026-06-18"})
    j = health_to_json(rep)
    assert j["status"] == WARN and j["real_orders_placed"] == 0 and isinstance(j["findings"], list)
    json.dumps(j)   # 직렬화 가능
    md = format_health_markdown(rep)
    assert "Shadow Run Health Check" in md and "real_orders_placed = 0" in md


# --- 러너 ---


def _df(periods=5, last="2026-06-18"):
    idx = pd.bdate_range(end=last, periods=periods)
    closes = [100.0 + i for i in range(periods)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": [1e6] * periods}, index=idx)


def test_runner_reads_data_and_ledgers(monkeypatch, tmp_path):
    monkeypatch.setattr(shc.run_sim, "load_norgate_folder",
                        lambda root: {"MU": _df(), "NVDA": _df(), "SPY": _df()})
    dec = tmp_path / "dec.jsonl"
    dec.write_text('{"date":"2026-06-18","symbol":"MU","decision":"BUY","real_orders_placed":0}\n'
                   "not-json-line\n", encoding="utf-8")     # malformed 1줄
    report, error = shc.run_shadow_health(data_root="x", decision_ledger=str(dec),
                                          outcome_ledger=str(tmp_path / "missing.jsonl"))
    assert error is None
    assert report.malformed == 1 and report.status == FAIL   # malformed → FAIL
    assert report.real_orders_placed == 0


def test_run_sim_defaults_unchanged():
    args = shc.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None
