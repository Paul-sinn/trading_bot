"""decision_log 테스트 (spec: specs/decision_log.md).

시그널 결정 로그(실험 전용). 기존 dry-run 산출물을 읽어 BUY/REJECT/SKIP 기록만 한다 — 스캐너/디시전/
사이징/RiskGate/베이스라인 미변경. 브로커/라이브 경로 없음. real_orders=0. 네트워크 없음.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.decision import Decision
from agents.decision_log import (
    DECISIONS,
    PLANNED_ENTRY_TYPE,
    DecisionRecord,
    build_decision_log,
    format_decision_log_markdown,
    make_record,
    records_to_jsonl,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiments.signal_decision_log as sdl  # noqa: E402


def _snap(mom=0.5, vol=1.3, up=True):
    return SimpleNamespace(momentum_score=mom, volume_ratio_20d=vol, price_above_20ma=up,
                           ma20_above_ma50=True, relative_strength=0.1, distance_from_high=-0.02)


# --- 레코드 / 필수 필드 ---


def test_make_record_required_fields_and_plan_constants():
    r = make_record("2026-06-18", "NVDA", "BUY", reason="weight ok", snapshot=_snap(),
                    shadow_score=0.8, riskgate_passed=True, position_shares=2.0)
    assert r.date == "2026-06-18" and r.symbol == "NVDA" and r.decision == "BUY"
    assert r.momentum_score == 0.5 and r.volume_ratio_20d == 1.3 and r.price_above_20ma is True
    assert r.shadow_score == 0.8 and r.riskgate_passed is True and r.position_shares == 2.0
    # planned entry/exit는 잠긴 베이스라인을 서술.
    assert r.planned_entry_type == PLANNED_ENTRY_TYPE == "next-bar-limit"
    assert r.entry_limit_buffer_pct == 0.03 and r.planned_stop_loss == 0.15
    assert r.planned_trailing_stop == 0.20 and r.planned_max_holding == 60
    assert r.real_orders_placed == 0


def test_make_record_rejects_bad_decision():
    with pytest.raises(ValueError):
        make_record("2026-06-18", "NVDA", "MAYBE")


def test_decisions_represented_consistently():
    recs = [make_record("d", "A", "BUY"), make_record("d", "B", "REJECT", riskgate_passed=False,
                                                       riskgate_reasons=("VETO: cap",)),
            make_record("d", "C", "SKIP")]
    log = build_decision_log("d", recs)
    assert (log.n_buy, log.n_reject, log.n_skip) == (1, 1, 1)
    assert log.riskgate_vetoes == 1
    assert set(DECISIONS) == {"BUY", "REJECT", "SKIP"}
    assert log.real_orders_placed == 0


# --- JSONL ---


def test_records_to_jsonl_valid_json_lines():
    recs = [make_record("2026-06-18", "NVDA", "BUY", snapshot=_snap(), riskgate_passed=True),
            make_record("2026-06-18", "MU", "REJECT", riskgate_passed=False, riskgate_reasons=("VETO: x",))]
    text = records_to_jsonl(recs)
    lines = text.splitlines()
    assert len(lines) == 2
    obj = json.loads(lines[0])
    assert obj["symbol"] == "NVDA" and obj["decision"] == "BUY" and obj["real_orders_placed"] == 0
    assert obj["planned_entry_type"] == "next-bar-limit"
    assert json.loads(lines[1])["riskgate_reasons"] == ["VETO: x"]


# --- 마크다운 6개 질문 ---


def test_markdown_answers_six_questions():
    recs = [make_record("2026-06-18", "NVDA", "BUY", reason="weight ok", snapshot=_snap(), riskgate_passed=True),
            make_record("2026-06-18", "MU", "REJECT", reason="weight 제안: rejected",
                        riskgate_passed=False, riskgate_reasons=("VETO: account_loss > cap",)),
            make_record("2026-06-18", "AAPL", "SKIP", reason="스캐너 후보 미선정")]
    md = format_decision_log_markdown(build_decision_log("2026-06-18", recs))
    assert "오늘 어떤 심볼을 살까" in md and "NVDA" in md
    assert "거절됐나" in md and "MU" in md
    assert "account_loss > cap" in md                 # 거절 사유
    assert "RiskGate가 무언가 veto했나" in md
    assert "주문 계획은" in md and "report-only" in md.lower() or "report-only" in md
    assert "real_orders_placed = 0" in md
    assert "SKIP" in md and "AAPL" in md


# --- 러너: 분류 / 출력 파일 / 브로커 미사용 ---


def _veto(passed, reasons=()):
    return SimpleNamespace(passed=passed, reasons=tuple(reasons))


def _decision_row(symbol, effective, *, passed=True, reasons=(), rationale="weight ok"):
    return SimpleNamespace(symbol=symbol, effective_decision=effective, raw_decision=Decision.BUY,
                           veto=_veto(passed, reasons), rationale=rationale)


def test_locked_constants_and_run_sim_defaults():
    assert sdl._FILL_MODEL == "next-bar-limit" and sdl._BUFFER == 0.03
    assert sdl._STOP == 0.15 and sdl._TRAIL == 0.20 and sdl._MAX_HOLD == 60 and sdl._SHARE_MODE == "fractional"
    args = sdl.run_sim.build_arg_parser().parse_args(["--data-root", "x"])
    assert args.entry_fill_model == "current" and args.max_holding_days is None and args.symbols is None


def test_runner_classifies_and_writes_files(monkeypatch, tmp_path):
    report = SimpleNamespace(
        report_date="2026-06-18",
        decisions=(
            _decision_row("NVDA", Decision.BUY, passed=True),
            _decision_row("MU", Decision.HOLD, passed=False, reasons=("VETO: account_loss > cap",),
                          rationale="weight 제안: rejected"),
        ),
    )
    last = SimpleNamespace(report=report, portfolio=SimpleNamespace(
        positions={"NVDA": SimpleNamespace(shares=2.0)}))
    multiday = SimpleNamespace(day_results=(last,), portfolio=last.portfolio)

    monkeypatch.setattr(sdl.run_sim, "_feature_inputs",
                        lambda a: ({"NVDA": object(), "MU": object(), "AAPL": object()}, None))
    monkeypatch.setattr(sdl, "compute_feature_diagnostics",
                        lambda md, pd, benchmark_prices=None, source_trades=None: SimpleNamespace(
                            rows=tuple(SimpleNamespace(symbol=t.symbol, context_date=t.entry_date,
                                                       snapshot=_snap()) for t in source_trades)))
    monkeypatch.setattr(sdl, "shadow_score", lambda snap: 0.7)
    captured = {}

    def _fake(args):
        captured["args"] = args
        return SimpleNamespace(multiday=multiday, real_orders_placed=0)

    monkeypatch.setattr(sdl.run_sim, "simulate", _fake)   # run() 경로(simulate_fn 미전달)용
    settings = dict(data_root="x", benchmark="SPY", events_csv=None, assume_no_events=True,
                    starting_cash=1000.0)
    date, records, error = sdl.build_decision_records(simulate_fn=_fake, settings=settings)
    assert error is None and date == "2026-06-18"
    # 진입 잠금 확인.
    a = captured["args"]
    assert a.entry_fill_model == "next-bar-limit" and a.entry_limit_buffer_pct == 0.03
    assert a.max_holding_days == 60 and a.stop_loss_pct == 0.15 and a.trailing_stop_pct == 0.20
    assert tuple(a.weekend_exit_symbols) == ()
    by = {r.symbol: r for r in records}
    assert by["NVDA"].decision == "BUY" and by["NVDA"].position_shares == 2.0
    assert by["MU"].decision == "REJECT" and by["MU"].riskgate_passed is False
    assert by["AAPL"].decision == "SKIP"               # 스캔 안 됨

    # 출력 파일 생성.
    md_path = tmp_path / "log.md"
    jsonl_path = tmp_path / "log.jsonl"
    rc = sdl.run(SimpleNamespace(
        data_root="x", benchmark="SPY", date=None, starting_cash=1000.0, events_csv="x",
        assume_no_events=True, output_md=str(md_path), output_jsonl=str(jsonl_path)))
    assert rc == 0
    assert md_path.exists() and jsonl_path.exists()
    assert "real_orders_placed = 0" in md_path.read_text(encoding="utf-8")
    # jsonl append-friendly: 다시 실행하면 라인이 누적.
    sdl.run(SimpleNamespace(
        data_root="x", benchmark="SPY", date=None, starting_cash=1000.0, events_csv="x",
        assume_no_events=True, output_md=str(md_path), output_jsonl=str(jsonl_path)))
    lines = [l for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 6                              # 3 레코드 × 2회 append
    assert all(json.loads(l)["real_orders_placed"] == 0 for l in lines)
