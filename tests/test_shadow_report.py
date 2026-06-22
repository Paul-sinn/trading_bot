"""shadow_report view 서비스 테스트 (spec: specs/shadow_view.md).

reports/ 산출물 → UI view model. 파일 없음/ malformed 안전. 거래소/LLM/주문 없음. real_orders=0.
스캐너/디시전/RiskGate/베이스라인 미변경(읽기 전용). 네트워크 없음.
"""

import json

from backend.app.services.shadow_report import ShadowReportView, load_shadow_report


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def _decision(date, symbol, decision, **kw):
    base = {"date": date, "symbol": symbol, "decision": decision, "real_orders_placed": 0,
            "planned_entry_type": "next-bar-limit", "entry_limit_buffer_pct": 0.03,
            "planned_stop_loss": 0.15, "planned_trailing_stop": 0.20, "planned_max_holding": 60,
            "position_shares": 0.0}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _outcome(date, symbol, decision, ret60, *, reentry=True, scorable=True):
    return json.dumps({
        "date": date, "symbol": symbol, "decision": decision, "real_orders_placed": 0,
        "outcome": {"scorable": scorable, "returns": {"1": 0.01, "5": 0.02, "10": None,
                                                       "20": None, "60": ret60}},
        "reentry": {"available": True, "is_reentry": reentry},
    }, ensure_ascii=False)


# --- 빈 상태 ---


def test_missing_files_empty_state_no_crash(tmp_path):
    view = load_shadow_report(reports_dir=tmp_path)   # 빈 디렉토리
    assert isinstance(view, ShadowReportView)
    assert view.available is False
    assert "python -m experiments.daily_shadow_report" in view.empty_message
    assert view.real_orders_placed == 0


# --- malformed 안전 ---


def test_malformed_jsonl_skipped(tmp_path):
    _write(tmp_path / "signal_decision_log.jsonl",
           _decision("2026-06-18", "NVDA", "BUY") + "\nnot-json\n{broken\n")
    view = load_shadow_report(reports_dir=tmp_path)
    assert view.available is True
    assert view.n_buy == 1                      # 유효 1행만, malformed 무시(크래시 없음)


def test_malformed_health_json_unknown(tmp_path):
    _write(tmp_path / "shadow_health_check.json", "{not valid json")
    _write(tmp_path / "signal_decision_log.jsonl", _decision("2026-06-18", "MU", "SKIP"))
    view = load_shadow_report(reports_dir=tmp_path)
    assert view.health_status == "UNKNOWN"      # malformed health → UNKNOWN(크래시 없음)


# --- 헬스 PASS/WARN/FAIL ---


def test_health_status_displayed(tmp_path):
    for status in ("PASS", "WARN", "FAIL"):
        _write(tmp_path / "shadow_health_check.json", json.dumps({
            "status": status, "report_date": "2026-06-18", "reference_date": "2026-06-18",
            "findings": [{"check": "stale_symbols", "status": "WARN", "message": "x"}],
        }))
        view = load_shadow_report(reports_dir=tmp_path)
        assert view.health_status == status
        assert view.health_findings[0].check == "stale_symbols"


# --- 카운트 / BUY 테이블 / pending·matured / 재진입 ---


def test_counts_buys_outcomes_reentry(tmp_path):
    _write(tmp_path / "signal_decision_log.jsonl", "\n".join([
        _decision("2026-06-18", "NVDA", "BUY", position_shares=2.0),
        _decision("2026-06-18", "MU", "REJECT", riskgate_passed=False),
        _decision("2026-06-18", "AAPL", "SKIP"),
        _decision("2026-06-10", "TSLA", "BUY"),     # 과거 날짜(오늘 카운트 제외)
    ]))
    _write(tmp_path / "decision_outcome_score.jsonl", "\n".join([
        _outcome("2026-04-01", "MU", "BUY", 0.5, reentry=True),
        _outcome("2026-06-18", "AMD", "BUY", None, reentry=False),   # 60d pending
    ]))
    view = load_shadow_report(reports_dir=tmp_path)
    assert (view.n_buy, view.n_reject, view.n_skip) == (1, 1, 1)    # 최신 날짜만
    assert view.riskgate_vetoes == 1
    assert view.buys[0].symbol == "NVDA" and view.buys[0].position_shares == 2.0
    assert view.buys[0].planned_entry_type == "next-bar-limit"
    assert view.matured_counts["60"] == 1 and view.pending_counts["60"] == 1
    assert view.reentry_total == 2 and view.reentry_count == 1
    assert view.real_orders_placed == 0


def test_concentration_warning_from_daily_md(tmp_path):
    _write(tmp_path / "daily_shadow_report.md",
           "# Daily\n- ⚠️ BUY 60d 양수 수익이 MU에 52% 집중 — 소수 종목 의존\n")
    view = load_shadow_report(reports_dir=tmp_path)
    assert any("집중" in w for w in view.concentration_warnings)
    assert view.daily_markdown is not None


def test_real_orders_violation_surfaced(tmp_path):
    _write(tmp_path / "signal_decision_log.jsonl",
           _decision("2026-06-18", "NVDA", "BUY", real_orders_placed=2))
    view = load_shadow_report(reports_dir=tmp_path)
    assert view.real_orders_placed != 0          # 불변식 위반은 숨기지 않고 노출
