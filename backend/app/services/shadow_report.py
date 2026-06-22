"""섀도 리포트 view 서비스 — report-only 산출물(reports/*)을 UI용 view model로 읽는다.

experiments 파이프라인(signal_decision_log / decision_outcome_score / daily_shadow_report /
shadow_health_check)이 만든 파일만 읽는다. 거래소/LLM/DB를 호출하지 않고, 스캐너/디시전/RiskGate/
베이스라인을 바꾸지 않는다. 파일 없음/ malformed는 안전하게 빈 상태로 처리(크래시 없음).

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM/뉴스 API 미연결.

spec: specs/shadow_view.md
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_REPORTS = _REPO_ROOT / "reports"
_HORIZONS = ("1", "5", "10", "20", "60")
_RUN_COMMAND = "python -m experiments.daily_shadow_report"

_DECISION_LOG = "signal_decision_log.jsonl"
_OUTCOME_LOG = "decision_outcome_score.jsonl"
_DAILY_MD = "daily_shadow_report.md"
_HEALTH_JSON = "shadow_health_check.json"


class HealthFindingView(BaseModel):
    check: str
    status: str
    message: str


# 잠긴 베이스라인을 '서술'하는 plan 기본값(변경 아님 — 누락 시 fallback).
_PLAN_ENTRY_TYPE = "next-bar-limit"
_PLAN_BUFFER = 0.03
_PLAN_STOP = 0.15
_PLAN_TRAIL = 0.20
_PLAN_MAX_HOLD = 60


class BuyView(BaseModel):
    symbol: str
    decision_date: str | None = None
    reason: str = ""
    # 시그널 지표(있으면; 없으면 None — 안전).
    shadow_score: float | None = None
    momentum_score: float | None = None
    volume_ratio_20d: float | None = None
    price_above_20ma: bool | None = None
    ma20_above_ma50: bool | None = None
    relative_strength: float | None = None
    distance_from_high: float | None = None
    # RiskGate 결과.
    riskgate_passed: bool | None = None
    riskgate_reasons: list[str] = []
    riskgate_result: str = "N/A"          # PASS | VETO | N/A
    # 포지션 상태.
    position_shares: float = 0.0
    position_state: str = "flat"          # held | flat
    # 재진입 컨텍스트(결과 원장에서 (date,symbol) 매칭; 없으면 None).
    is_reentry: bool | None = None
    previous_exit_reason: str | None = None
    days_since_last_exit: int | None = None
    # 주문 계획(잠긴 베이스라인 서술 — report-only).
    planned_entry_type: str = _PLAN_ENTRY_TYPE
    entry_limit_buffer_pct: float = _PLAN_BUFFER
    planned_stop_loss: float = _PLAN_STOP
    planned_trailing_stop: float = _PLAN_TRAIL
    planned_max_holding: int = _PLAN_MAX_HOLD
    real_orders_placed: int = 0


class OutcomeRowView(BaseModel):
    date: str
    symbol: str
    decision: str
    return_60d: float | None
    scorable: bool


class ShadowReportView(BaseModel):
    available: bool
    empty_message: str | None = None
    run_command: str = _RUN_COMMAND
    health_status: str = "UNKNOWN"
    health_findings: list[HealthFindingView] = []
    report_date: str | None = None
    reference_date: str | None = None
    selected_date: str | None = None
    available_dates: list[str] = []
    n_buy: int = 0
    n_reject: int = 0
    n_skip: int = 0
    riskgate_vetoes: int = 0
    real_orders_placed: int = 0
    buys: list[BuyView] = []
    pending_counts: dict[str, int] = {}
    matured_counts: dict[str, int] = {}
    recent_outcomes: list[OutcomeRowView] = []
    reentry_total: int = 0
    reentry_count: int = 0
    concentration_warnings: list[str] = []
    daily_markdown: str | None = None


def _read_json_safe(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl_safe(path: Path) -> list[dict]:
    """JSONL을 안전하게 읽는다. malformed/비-dict 행은 건너뛴다(크래시 없음)."""
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _latest_date(records) -> str | None:
    dates = [str(r.get("date")) for r in records if r.get("date")]
    return max(dates) if dates else None


def _unique_dates_desc(records) -> list[str]:
    return sorted({str(r.get("date")) for r in records if r.get("date")}, reverse=True)


def _optfloat(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _optint(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _optbool(v):
    return None if v is None else bool(v)


def _gate_result(passed) -> str:
    if passed is False:
        return "VETO"
    if passed is True:
        return "PASS"
    return "N/A"


def _plan_float(v, default):
    f = _optfloat(v)
    return default if f is None else f


def _buy_view(rec: dict, reentry_index: dict) -> BuyView:
    """BUY 결정 레코드를 사전검토 view로(누락/타입오류 안전). 재진입은 결과 원장에서 매칭."""
    shares = _optfloat(rec.get("position_shares")) or 0.0
    re = reentry_index.get((str(rec.get("date")), str(rec.get("symbol"))), {})
    prev_exit = re.get("previous_exit_reason")
    return BuyView(
        symbol=str(rec.get("symbol")),
        decision_date=(str(rec.get("date")) if rec.get("date") else None),
        reason=str(rec.get("reason", "") or ""),
        shadow_score=_optfloat(rec.get("shadow_score")),
        momentum_score=_optfloat(rec.get("momentum_score")),
        volume_ratio_20d=_optfloat(rec.get("volume_ratio_20d")),
        price_above_20ma=_optbool(rec.get("price_above_20ma")),
        ma20_above_ma50=_optbool(rec.get("ma20_above_ma50")),
        relative_strength=_optfloat(rec.get("relative_strength")),
        distance_from_high=_optfloat(rec.get("distance_from_high")),
        riskgate_passed=_optbool(rec.get("riskgate_passed")),
        riskgate_reasons=[str(x) for x in (rec.get("riskgate_reasons") or [])],
        riskgate_result=_gate_result(rec.get("riskgate_passed")),
        position_shares=shares,
        position_state=("held" if shares > 0 else "flat"),
        is_reentry=_optbool(re.get("is_reentry")),
        previous_exit_reason=(str(prev_exit) if prev_exit is not None else None),
        days_since_last_exit=_optint(re.get("days_since_last_exit")),
        planned_entry_type=str(rec.get("planned_entry_type") or _PLAN_ENTRY_TYPE),
        entry_limit_buffer_pct=_plan_float(rec.get("entry_limit_buffer_pct"), _PLAN_BUFFER),
        planned_stop_loss=_plan_float(rec.get("planned_stop_loss"), _PLAN_STOP),
        planned_trailing_stop=_plan_float(rec.get("planned_trailing_stop"), _PLAN_TRAIL),
        planned_max_holding=(_optint(rec.get("planned_max_holding")) or _PLAN_MAX_HOLD),
        real_orders_placed=0,
    )


def _ret60(rec) -> float | None:
    returns = (rec.get("outcome", {}) or {}).get("returns", {}) or {}
    return returns.get("60", returns.get(60))


def load_shadow_report(reports_dir=None, date=None) -> ShadowReportView:
    """reports/ 산출물을 읽어 UI view model을 만든다. 파일 없음/ malformed 안전.

    date 지정 시 해당 거래일로 필터해 과거 BUY 예시를 리뷰한다(읽기 전용 — 원장 미변경).
    """
    base = Path(reports_dir) if reports_dir is not None else _DEFAULT_REPORTS

    health = _read_json_safe(base / _HEALTH_JSON)
    decisions = _read_jsonl_safe(base / _DECISION_LOG)
    outcomes = _read_jsonl_safe(base / _OUTCOME_LOG)
    daily_md_path = base / _DAILY_MD
    daily_md = daily_md_path.read_text(encoding="utf-8") if daily_md_path.exists() else None

    if not health and not decisions and not outcomes and daily_md is None:
        return ShadowReportView(
            available=False,
            empty_message=f"섀도 리포트가 아직 없습니다. 먼저 실행하세요: {_RUN_COMMAND}",
        )

    # 헬스(json).
    health_status = "UNKNOWN"
    findings: list[HealthFindingView] = []
    reference_date = None
    health_report_date = None
    if isinstance(health, dict):
        health_status = str(health.get("status", "UNKNOWN"))
        reference_date = health.get("reference_date")
        health_report_date = health.get("report_date")
        for f in health.get("findings", []) or []:
            if isinstance(f, dict):
                findings.append(HealthFindingView(
                    check=str(f.get("check", "")), status=str(f.get("status", "")),
                    message=str(f.get("message", "")),
                ))

    # 거래일 결정 카운트. date 지정 시 그 날짜(과거 BUY 예시 리뷰), 아니면 최신.
    available_dates = _unique_dates_desc(decisions)
    selected_date = str(date) if date else None
    report_date = selected_date or health_report_date or _latest_date(decisions)
    today = [r for r in decisions if str(r.get("date")) == str(report_date)] if report_date else []
    n_buy = sum(1 for r in today if r.get("decision") == "BUY")
    n_reject = sum(1 for r in today if r.get("decision") == "REJECT")
    n_skip = sum(1 for r in today if r.get("decision") == "SKIP")
    vetoes = sum(1 for r in today if r.get("decision") == "REJECT" and r.get("riskgate_passed") is False)

    # 재진입 컨텍스트 인덱스((date,symbol) → reentry). BUY 사전검토에 머지.
    reentry_index = {
        (str(r.get("date")), str(r.get("symbol"))): (r.get("reentry") or {})
        for r in outcomes if (r.get("reentry") or {}).get("available")
    }
    buys = [_buy_view(r, reentry_index) for r in today if r.get("decision") == "BUY"]

    # 결과 원장: pending/matured(BUY), 재진입, 최근.
    buy_outcomes = [r for r in outcomes if r.get("decision") == "BUY"
                    and (r.get("outcome", {}) or {}).get("scorable")]
    pending = {h: sum(1 for r in buy_outcomes if _hret(r, h) is None) for h in _HORIZONS}
    matured = {h: sum(1 for r in buy_outcomes if _hret(r, h) is not None) for h in _HORIZONS}
    reentry_rows = [r for r in outcomes if (r.get("reentry", {}) or {}).get("available")]
    reentry_count = sum(1 for r in reentry_rows if (r.get("reentry", {}) or {}).get("is_reentry"))

    recent = sorted(outcomes, key=lambda r: str(r.get("date")), reverse=True)[:15]
    recent_outcomes = [OutcomeRowView(
        date=str(r.get("date")), symbol=str(r.get("symbol")), decision=str(r.get("decision")),
        return_60d=_ret60(r), scorable=bool((r.get("outcome", {}) or {}).get("scorable")),
    ) for r in recent]

    # 집중 경고: daily md의 ⚠️ 라인 중 '집중'.
    concentration = []
    if daily_md:
        for line in daily_md.splitlines():
            if "집중" in line and "⚠️" in line:
                concentration.append(line.strip().lstrip("- ").lstrip("⚠️ ").strip())

    # real_orders 검증: 어떤 행이든 0이 아니면 그대로 노출(불변식 위반 가시화). 기본 0.
    bad = any(r.get("real_orders_placed", 0) not in (0, None) for r in decisions + outcomes)

    return ShadowReportView(
        available=True, health_status=health_status, health_findings=findings,
        report_date=report_date, reference_date=reference_date,
        selected_date=selected_date, available_dates=available_dates,
        n_buy=n_buy, n_reject=n_reject, n_skip=n_skip, riskgate_vetoes=vetoes,
        real_orders_placed=(1 if bad else 0),
        buys=buys, pending_counts=pending, matured_counts=matured,
        recent_outcomes=recent_outcomes, reentry_total=len(reentry_rows), reentry_count=reentry_count,
        concentration_warnings=concentration, daily_markdown=daily_md,
    )


def _hret(rec, h):
    returns = (rec.get("outcome", {}) or {}).get("returns", {}) or {}
    return returns.get(h, returns.get(int(h)))
