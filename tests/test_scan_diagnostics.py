"""라이브 스캔 진단 v1 테스트 — 진단 전용(주문/승인/Robinhood write/Alpaca 거래 없음).

검증: 모든 스캔 심볼에 진단 생성 · BUY는 긍정 사유 · skip은 사람 친화 사유 · 데이터부족/레짐차단 사유 친화 ·
요약 헤드라인/근접후보 · API 최신/목록 · 부수효과(승인/주문) 없음 · write 도구/Alpaca 거래 미사용.

spec: specs/real_order_v1_checklist.md §19
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.services.live_scan import ScanEvent, _scan_path
import backend.app.services.scan_diagnostics as sd
from backend.app.services.scan_diagnostics import (
    build_symbol_diagnostic,
    latest_diagnostics,
    recent_diagnostics,
)
from backend.app.main import app
import json


def _ev(symbol, scan_status, reason, *, trend="UP", rs=True, regime="NORMAL_BULL",
        regime_source="spy+vix", vix=15.0, risk_reduced=False, warn=None, price=100.0,
        buy=False) -> ScanEvent:
    return ScanEvent(
        timestamp="2026-06-23T15:00:00+00:00", symbol=symbol, scan_status=scan_status, reason=reason,
        price=price, buy_candidate=buy, regime_source=regime_source, vix_value=vix,
        risk_reduced=risk_reduced, regime_warning=warn,
        features={"trend": trend, "relative_strength": rs, "rsi": 55.0, "regime": regime, "price": price},
    )


def _write_cycle(tmp_path, events):
    path = _scan_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e.model_dump(), ensure_ascii=False) + "\n")


# --- 종목 진단 매핑 ---
def test_buy_candidate_positive_reason():
    d = build_symbol_diagnostic(_ev("F", "BUY_CANDIDATE", "진입: 게이트 통과 + 눌림목 재개", buy=True))
    assert d.final_decision == "BUY_CANDIDATE" and "매수 후보" in d.human_reason
    assert d.signal_strength == "통과" and "재개 신호" in d.pullback_status


def test_skipped_timing_reason_user_friendly():
    d = build_symbol_diagnostic(_ev("AAPL", "SKIP", "트리거 미충족: 눌림 없음(눌림 대기)"))
    assert d.final_decision == "SKIPPED"
    assert "매수 타이밍" in d.human_reason and "기술" not in d.human_reason.lower()
    assert d.trend_status == "상승 추세" and d.technical_reason.startswith("트리거")


def test_missing_data_reason_user_friendly():
    d = build_symbol_diagnostic(_ev("MSFT", "INSUFFICIENT_DATA", "bars < 200(추세 워밍업 전)", trend=None, rs=None, regime="NORMAL_BULL"))
    assert d.final_decision == "SKIPPED" and "데이터가 부족" in d.human_reason
    assert d.data_status == "데이터 부족/오류"


def test_regime_blocked_reason_user_friendly():
    d = build_symbol_diagnostic(_ev("NVDA", "REJECT", "게이트 실패: 레짐 BEARISH 신규 진입 불가", regime="BEARISH"))
    assert d.final_decision == "SKIPPED" and "시장 위험도가 높아" in d.human_reason
    assert d.regime_status == "약세 — 신규 매수 제한"


def test_error_reason_user_friendly():
    d = build_symbol_diagnostic(_ev("F", "ERROR", "데이터 조회 실패: X", trend=None, rs=None))
    assert d.final_decision == "ERROR" and "오류" in d.human_reason


def test_advanced_fields_separate_from_human():
    d = build_symbol_diagnostic(_ev("F", "SKIP", "트리거 미충족: 재개 신호 없음"))
    # 사람 친화(human_reason) ≠ 기술(technical_reason/scan_status/regime).
    assert d.human_reason != d.technical_reason
    assert d.scan_status == "SKIP" and d.regime == "NORMAL_BULL" and d.regime_source == "spy+vix"


# --- 요약 ---
def test_summary_no_candidates_headline(tmp_path):
    evs = [_ev("F", "SKIP", "트리거 미충족: 눌림 없음(눌림 대기)"),
           _ev("AAPL", "SKIP", "트리거 미충족: 재개 신호 없음", rs=True),
           _ev("NVDA", "REJECT", "게이트 실패: SPY 대비 상대강도 미충족", rs=False)]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert view.summary.total_scanned == 3 and view.summary.buy_candidates == 0
    assert "매수 타이밍을 통과한 종목이 없습니다" in view.summary.headline
    assert view.summary.market_condition == "시장 상태: 매수 가능 구간"
    assert len(view.summary.top_closest) >= 1 and view.summary.main_skip_reason


def test_summary_with_candidate_headline(tmp_path):
    evs = [_ev("F", "BUY_CANDIDATE", "진입: 게이트 통과 + 눌림목 재개", buy=True),
           _ev("AAPL", "SKIP", "트리거 미충족: 눌림 없음")]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert view.summary.buy_candidates == 1 and "통과했습니다" in view.summary.headline


def test_summary_bearish_headline(tmp_path):
    evs = [_ev("F", "REJECT", "게이트 실패: 레짐 BEARISH 신규 진입 불가", regime="BEARISH"),
           _ev("AAPL", "REJECT", "게이트 실패: 레짐 BEARISH 신규 진입 불가", regime="BEARISH")]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert "위험도가 높아" in view.summary.headline and view.summary.skipped == 2


def test_vix_warning_surfaced(tmp_path):
    evs = [_ev("F", "SKIP", "트리거 미충족: 눌림 없음", regime="spy_bull_vix_unknown",
               regime_source="spy_only", vix=None, risk_reduced=True,
               warn="VIX unavailable, using SPY-only conservative regime")]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert view.summary.vix_warning and "SPY-only" in view.summary.vix_warning
    assert view.summary.risk_reduced is True and view.summary.regime_source == "spy_only"


def test_every_symbol_gets_diagnostic(tmp_path):
    evs = [_ev(s, "SKIP", "트리거 미충족: 눌림 없음") for s in ("F", "AAPL", "NVDA", "MSFT")]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert {d.symbol for d in view.symbols} == {"F", "AAPL", "NVDA", "MSFT"}
    assert all(d.human_reason for d in view.symbols)


def test_latest_cycle_only(tmp_path):
    # 두 사이클을 쓰면 최신 사이클만(심볼 반복 직전까지).
    evs = [_ev("F", "SKIP", "x"), _ev("AAPL", "SKIP", "x"),  # 이전 사이클
           _ev("F", "BUY_CANDIDATE", "진입: 게이트 통과 + 눌림목 재개", buy=True), _ev("AAPL", "SKIP", "y")]
    _write_cycle(tmp_path, evs)
    view = latest_diagnostics(reports_dir=tmp_path)
    assert view.summary.total_scanned == 2 and view.summary.buy_candidates == 1


# --- API ---
def test_api_latest_and_list(tmp_path, monkeypatch):
    import backend.app.services.live_scan as ls
    monkeypatch.setattr(ls, "DEFAULT_REPORTS_DIR", tmp_path)
    _write_cycle(tmp_path, [_ev("F", "SKIP", "트리거 미충족: 눌림 없음"),
                            _ev("AAPL", "BUY_CANDIDATE", "진입: 게이트 통과 + 눌림목 재개", buy=True)])
    c = TestClient(app)
    latest = c.get("/api/live/scan-diagnostics/latest").json()
    assert latest["summary"]["total_scanned"] == 2
    assert any(s["final_decision"] == "BUY_CANDIDATE" for s in latest["symbols"])
    assert all("human_reason" in s and "technical_reason" in s for s in latest["symbols"])  # 둘 다 반환
    lst = c.get("/api/live/scan-diagnostics?limit=10").json()
    assert isinstance(lst, list) and len(lst) >= 1


def test_empty_diagnostics_safe(tmp_path):
    view = latest_diagnostics(reports_dir=tmp_path)  # 스캔 기록 없음
    assert view.summary.total_scanned == 0 and "스캔 기록이 없습니다" in view.summary.headline
    assert recent_diagnostics(reports_dir=tmp_path) == []


# --- 안전 ---
def test_no_side_effects(tmp_path):
    _write_cycle(tmp_path, [_ev("F", "SKIP", "트리거 미충족: 눌림 없음")])
    latest_diagnostics(reports_dir=tmp_path)
    recent_diagnostics(reports_dir=tmp_path)
    assert not (tmp_path / "approval_requests.jsonl").exists()
    assert not (tmp_path / "real_execution_receipts.jsonl").exists()
    assert not (tmp_path / "order_router_decisions.jsonl").exists()


def test_no_robinhood_write_or_alpaca_trading():
    import inspect
    text = inspect.getsource(sd)
    assert "mcp__robinhood" not in text and "place_equity_order" not in text
    assert "submit" not in text and "place_order" not in text
