"""CandidatePipeline 테스트 (spec: specs/live_decision_pipeline.md).

BUY_CANDIDATE만 처리·dedupe·쿨다운·AI예산·approve→intent·veto/needs_review→no intent.
Stop/Emergency-Halt가 처리 차단. live_candidates/live_order_intents jsonl 기록(shadow 무관).
real_orders_placed=0, ai_cost=0.00.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.services.candidate_pipeline import (
    CANDIDATES_LOG,
    ORDER_INTENTS_LOG,
    CandidatePipeline,
)
from backend.app.services.live_scan import ScanEvent


def _event(symbol="NVDA", status="BUY_CANDIDATE", *, price=100.0, features=None, ts="2026-06-22T10:00:00+00:00"):
    full = {"trend": "UP", "relative_strength": True, "rsi": 55.0, "regime": "NORMAL_BULL", "price": price}
    return ScanEvent(
        timestamp=ts, session_id="s1", symbol=symbol, price=price,
        scan_status=status, reason="", features=full if features is None else features,
        buy_candidate=(status == "BUY_CANDIDATE"),
    )


def _pipeline(tmp_path, **settings_kw):
    base = dict(min_llm_cooldown_seconds_per_symbol=900, max_llm_calls_per_day=50)
    base.update(settings_kw)
    return CandidatePipeline(settings=Settings(**base), reports_dir=tmp_path)


def _process(p, events, *, session_id="s1"):
    return p.process_scan_events(
        events, session_id=session_id, trading_mode="report_only",
        automation_running=True, emergency_halt=False,
    )


def test_buy_candidate_creates_candidate(tmp_path):
    cands = _process(_pipeline(tmp_path), [_event()])
    assert len(cands) == 1
    assert cands[0].symbol == "NVDA"
    assert cands[0].status == "approved"
    assert (tmp_path / CANDIDATES_LOG).exists()


def test_non_buy_event_creates_no_candidate(tmp_path):
    cands = _process(_pipeline(tmp_path), [_event(status="SKIP"), _event(status="REJECT")])
    assert cands == []


def test_duplicate_candidate_is_deduped(tmp_path):
    p = _pipeline(tmp_path)
    _process(p, [_event()])
    again = _process(p, [_event()])  # 같은 session|symbol|date|strategy → dedupe
    assert again == []


def test_cooldown_blocks_repeated_review(tmp_path):
    p = _pipeline(tmp_path)
    _process(p, [_event()], session_id="s1")  # NVDA 리뷰됨
    # 다른 session_id(=다른 key)지만 같은 심볼·쿨다운 내 → LLM_COOLDOWN_ACTIVE.
    cands = _process(p, [_event()], session_id="s2")
    assert len(cands) == 1
    assert cands[0].block_reason == "LLM_COOLDOWN_ACTIVE"
    assert cands[0].review is None  # 리뷰 호출 안 함


def test_budget_blocks_when_calls_exceeded(tmp_path):
    p = _pipeline(tmp_path, max_llm_calls_per_day=1)
    _process(p, [_event(symbol="NVDA")])  # 1콜 소진
    cands = _process(p, [_event(symbol="AMD")])  # 한도 초과
    assert cands[0].block_reason == "AI_BUDGET_EXCEEDED"
    assert cands[0].review is None
    assert p.ai_status().ai_calls_today == 1  # 카운트 1에서 멈춤


def test_approved_creates_order_intent(tmp_path):
    p = _pipeline(tmp_path)
    _process(p, [_event()])
    intents = p.order_intents()
    assert len(intents) == 1
    oi = intents[0]
    assert oi.execution_gate_status == "accepted_dry_run"
    assert oi.real_orders_placed == 0
    assert oi.broker_order_id is None
    assert oi.status == "DRY_RUN_INTENT_ONLY"


def test_veto_creates_no_order_intent(tmp_path):
    p = _pipeline(tmp_path)
    cands = _process(p, [_event(status="BUY_CANDIDATE", features={"trend": "UP"})])  # 불완전 → needs_review
    # needs_review는 intent 없음.
    assert cands[0].status == "needs_review"
    assert p.order_intents() == []


def test_error_event_not_processed_as_buy(tmp_path):
    # ERROR/INSUFFICIENT_DATA는 BUY_CANDIDATE가 아니므로 후보 자체가 안 만들어진다.
    cands = _process(_pipeline(tmp_path), [_event(status="ERROR"), _event(status="INSUFFICIENT_DATA")])
    assert cands == []


def test_processing_blocked_when_not_running(tmp_path):
    p = _pipeline(tmp_path)
    cands = p.process_scan_events(
        [_event()], session_id="s1", trading_mode="report_only",
        automation_running=False, emergency_halt=False,
    )
    assert cands == []
    assert p.candidates() == []


def test_processing_blocked_when_emergency_halt(tmp_path):
    p = _pipeline(tmp_path)
    cands = p.process_scan_events(
        [_event()], session_id="s1", trading_mode="report_only",
        automation_running=True, emergency_halt=True,
    )
    assert cands == []


def test_ai_cost_always_zero(tmp_path):
    p = _pipeline(tmp_path)
    _process(p, [_event(symbol="NVDA"), _event(symbol="AMD")])
    assert p.ai_status().ai_cost_estimate_today == 0.0
    intents = p.order_intents()
    assert all(i.real_orders_placed == 0 for i in intents)


def test_writes_live_files_not_shadow(tmp_path):
    _process(_pipeline(tmp_path), [_event()])
    written = {f.name for f in tmp_path.iterdir()}
    assert CANDIDATES_LOG in written
    assert ORDER_INTENTS_LOG in written
    for shadow in ("signal_decision_log.jsonl", "decision_outcome_score.jsonl",
                   "daily_shadow_report.md", "shadow_health_check.json"):
        assert shadow not in written


def test_records_have_zero_real_orders(tmp_path):
    _process(_pipeline(tmp_path), [_event()])
    for line in (tmp_path / ORDER_INTENTS_LOG).read_text(encoding="utf-8").splitlines():
        if line.strip():
            assert json.loads(line)["real_orders_placed"] == 0


def test_pipeline_path_has_no_broker_or_llm_imports():
    import backend.app.services.candidate_pipeline as mod

    # import 라인만 검사(프로즈/주석 제외) — 실 LLM/브로커/HTTP 모듈을 import하지 않음을 보장.
    lines = Path(mod.__file__).read_text(encoding="utf-8").lower().splitlines()
    imports = "\n".join(x for x in lines if x.strip().startswith(("import ", "from ")))
    for forbidden in ("openai", "anthropic", "claude", "requests", "httpx", "robinhood", "yfinance"):
        assert forbidden not in imports, f"파이프라인이 {forbidden}를 import함"
