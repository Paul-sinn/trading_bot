"""섀도 런 헬스 체크 / 데이터 신선도 가드 — 일간 섀도 입력·원장을 검증한다(순수 측정).

신선도/커버리지/중복/malformed/필수필드/real_orders 점검은 순수 함수. 데이터·원장을 읽기만 하며
스캐너/디시전/RiskGate/베이스라인을 바꾸지 않는다. PASS/WARN/FAIL로 종합한다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/shadow_health.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_ORDER = {PASS: 0, WARN: 1, FAIL: 2}

_DECISION_REQUIRED = ("date", "symbol", "decision")
_OUTCOME_REQUIRED = ("date", "symbol", "decision", "outcome")


def worst_status(statuses) -> str:
    """가장 심각한 상태(FAIL > WARN > PASS). 비면 PASS."""
    return max(statuses, key=lambda s: _ORDER.get(s, 0), default=PASS)


@dataclass(frozen=True)
class HealthFinding:
    check: str
    status: str
    message: str


@dataclass(frozen=True)
class HealthReport:
    status: str
    findings: tuple
    n_symbols: int
    n_missing: int
    n_stale: int
    dup_decisions: int
    dup_outcomes: int
    malformed: int
    report_date: str | None
    reference_date: str | None
    counts: dict = field(default_factory=dict)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _diff_days(a, b):
    try:
        return (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days
    except (ValueError, TypeError):
        return None


def _dup_ids(records):
    seen, dups = set(), set()
    for r in records:
        rid = f"{r.get('date')}|{r.get('symbol')}|{r.get('decision')}"
        if rid in seen:
            dups.add(rid)
        seen.add(rid)
    return dups


def _missing_required(records, required):
    out = 0
    for r in records:
        if any(r.get(f) is None for f in required):
            out += 1
    return out


def _bad_real_orders(records):
    return sum(1 for r in records if r.get("real_orders_placed", 0) not in (0, None))


def build_health(*, universe=(), available_symbols=(), last_dates=None, report_date=None,
                 trading_days=None, as_of=None, decision_records=(), decision_malformed=0,
                 outcome_records=(), outcome_malformed=0, stale_days=5) -> HealthReport:
    """입력/원장 점검을 종합해 HealthReport를 만든다(순수)."""
    last_dates = last_dates or {}
    trading_days = set(trading_days or [])
    available = set(available_symbols)
    findings: list[HealthFinding] = []

    # 결측 심볼 / 부분 커버리지.
    missing = [s for s in universe if s not in available]
    if missing:
        findings.append(HealthFinding("missing_symbols", WARN,
                                      f"유니버스 {len(missing)}개 심볼 데이터 없음: {', '.join(sorted(missing))}"))

    # 신선도: 가장 신선한 날짜 대비 lag.
    reference = max(last_dates.values()) if last_dates else None
    stale = []
    if reference is not None:
        for sym, d in last_dates.items():
            lag = _diff_days(d, reference)
            if lag is not None and lag > stale_days:
                stale.append((sym, d, lag))
        if stale:
            findings.append(HealthFinding("stale_symbols", WARN,
                                          f"{len(stale)}개 심볼 stale(>{stale_days}일 lag): " +
                                          ", ".join(f"{s}({l}d)" for s, _, l in sorted(stale))))
    # 전체 데이터 나이(as_of 대비).
    if as_of is not None and reference is not None:
        age = _diff_days(reference, as_of)
        if age is not None and age > stale_days:
            findings.append(HealthFinding("data_age", WARN,
                                          f"최신 데이터({reference})가 as_of({as_of})보다 {age}일 뒤짐 — stale"))

    # report date 거래일 여부.
    if report_date is not None and trading_days and str(report_date) not in trading_days:
        findings.append(HealthFinding("report_date", WARN,
                                      f"report date {report_date}가 거래일 아님(로컬 데이터 밖)"))

    # 원장 malformed (FAIL).
    if decision_malformed or outcome_malformed:
        findings.append(HealthFinding("malformed_jsonl", FAIL,
                                      f"malformed JSONL 행: 결정 {decision_malformed} + 결과 {outcome_malformed}"))

    # 중복.
    dup_dec = _dup_ids(decision_records)
    dup_out = _dup_ids(outcome_records)
    if dup_dec:
        findings.append(HealthFinding("duplicate_decisions", WARN, f"결정 원장 중복 {len(dup_dec)}건"))
    if dup_out:
        findings.append(HealthFinding("duplicate_outcomes", WARN, f"결과 원장 중복 {len(dup_out)}건"))

    # 필수 필드.
    miss_dec = _missing_required(decision_records, _DECISION_REQUIRED)
    miss_out = _missing_required(outcome_records, _OUTCOME_REQUIRED)
    if miss_dec or miss_out:
        findings.append(HealthFinding("missing_fields", WARN,
                                      f"필수 필드 누락: 결정 {miss_dec} + 결과 {miss_out}"))

    # real_orders_placed != 0 (FAIL).
    bad = _bad_real_orders(decision_records) + _bad_real_orders(outcome_records)
    if bad:
        findings.append(HealthFinding("real_orders", FAIL,
                                      f"real_orders_placed != 0 인 행 {bad}건 — 안전 불변식 위반"))

    status = worst_status([f.status for f in findings]) if findings else PASS
    return HealthReport(
        status=status, findings=tuple(findings), n_symbols=len(available), n_missing=len(missing),
        n_stale=len(stale), dup_decisions=len(dup_dec), dup_outcomes=len(dup_out),
        malformed=decision_malformed + outcome_malformed, report_date=(str(report_date) if report_date else None),
        reference_date=(str(reference) if reference else None),
        counts=dict(decision_rows=len(decision_records), outcome_rows=len(outcome_records)),
    )


def health_to_json(report: HealthReport) -> dict:
    return {
        "status": report.status,
        "report_date": report.report_date,
        "reference_date": report.reference_date,
        "n_symbols": report.n_symbols,
        "n_missing": report.n_missing,
        "n_stale": report.n_stale,
        "dup_decisions": report.dup_decisions,
        "dup_outcomes": report.dup_outcomes,
        "malformed": report.malformed,
        "real_orders_placed": report.real_orders_placed,
        "findings": [{"check": f.check, "status": f.status, "message": f.message} for f in report.findings],
    }


def format_health_markdown(report: HealthReport) -> str:
    """사람이 읽는 헬스 체크(측정 보조 — 매매 미사용)."""
    badge = {PASS: "✅ PASS", WARN: "⚠️ WARN", FAIL: "❌ FAIL"}[report.status]
    lines: list[str] = []
    lines.append(f"# Shadow Run Health Check — {badge} (측정 - 실주문 없음)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 데이터·원장을 읽어 "
                 "검증만 한다 — 스캐너/디시전/사이징/RiskGate·진입/청산/유니버스·베이스라인 미변경.")
    lines.append("")
    lines.append(f"**상태: {report.status}** · report_date {report.report_date or '(latest)'} · "
                 f"reference {report.reference_date or 'n/a'} · 심볼 {report.n_symbols} "
                 f"(결측 {report.n_missing}, stale {report.n_stale}) · "
                 f"중복(결정/결과) {report.dup_decisions}/{report.dup_outcomes} · malformed {report.malformed}")
    lines.append("")
    lines.append("## findings")
    lines.append("")
    if report.findings:
        for f in report.findings:
            mark = {PASS: "✅", WARN: "⚠️", FAIL: "❌"}[f.status]
            lines.append(f"- {mark} **{f.check}** ({f.status}): {f.message}")
    else:
        lines.append("- ✅ 이상 없음 — 데이터·원장 사용 가능.")
    lines.append("")
    lines.append("## rules")
    lines.append("")
    lines.append("- FAIL: 원장 malformed 또는 real_orders_placed != 0.")
    lines.append("- WARN: stale 데이터 / 결측 심볼 / 비거래일 date / 중복 / 필드 누락.")
    lines.append("- PASS: 데이터·원장 사용 가능.")
    lines.append("")
    lines.append(f"`real_orders_placed = {report.real_orders_placed}`")
    lines.append("")
    return "\n".join(lines)
