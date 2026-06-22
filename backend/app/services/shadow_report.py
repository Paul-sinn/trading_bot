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


class BuyView(BaseModel):
    symbol: str
    planned_entry_type: str
    entry_limit_buffer_pct: float
    planned_stop_loss: float
    planned_trailing_stop: float
    planned_max_holding: int
    position_shares: float


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


def _ret60(rec) -> float | None:
    returns = (rec.get("outcome", {}) or {}).get("returns", {}) or {}
    return returns.get("60", returns.get(60))


def load_shadow_report(reports_dir=None) -> ShadowReportView:
    """reports/ 산출물을 읽어 UI view model을 만든다. 파일 없음/ malformed 안전."""
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

    # 최신 거래일 결정 카운트.
    report_date = health_report_date or _latest_date(decisions)
    today = [r for r in decisions if str(r.get("date")) == str(report_date)] if report_date else []
    n_buy = sum(1 for r in today if r.get("decision") == "BUY")
    n_reject = sum(1 for r in today if r.get("decision") == "REJECT")
    n_skip = sum(1 for r in today if r.get("decision") == "SKIP")
    vetoes = sum(1 for r in today if r.get("decision") == "REJECT" and r.get("riskgate_passed") is False)

    buys = [BuyView(
        symbol=str(r.get("symbol")),
        planned_entry_type=str(r.get("planned_entry_type", "next-bar-limit")),
        entry_limit_buffer_pct=float(r.get("entry_limit_buffer_pct", 0.03)),
        planned_stop_loss=float(r.get("planned_stop_loss", 0.15)),
        planned_trailing_stop=float(r.get("planned_trailing_stop", 0.20)),
        planned_max_holding=int(r.get("planned_max_holding", 60)),
        position_shares=float(r.get("position_shares", 0.0) or 0.0),
    ) for r in today if r.get("decision") == "BUY"]

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
        n_buy=n_buy, n_reject=n_reject, n_skip=n_skip, riskgate_vetoes=vetoes,
        real_orders_placed=(1 if bad else 0),
        buys=buys, pending_counts=pending, matured_counts=matured,
        recent_outcomes=recent_outcomes, reentry_total=len(reentry_rows), reentry_count=reentry_count,
        concentration_warnings=concentration, daily_markdown=daily_md,
    )


def _hret(rec, h):
    returns = (rec.get("outcome", {}) or {}).get("returns", {}) or {}
    return returns.get(h, returns.get(int(h)))
