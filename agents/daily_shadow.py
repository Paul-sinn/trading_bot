"""일간 섀도 리포트 / 전진 원장 — 결정 누적 + 성숙한 결과 채점을 멱등하게 묶는다(순수 측정).

원장 dedupe/upsert, pending/matured 분류, 일간 리포트/마크다운은 순수 함수. 결정 생성·채점은 기존
러너를 오케스트레이션할 뿐 — 스캐너/디시전/RiskGate/베이스라인을 바꾸지 않는다. 미성숙 horizon은
pending(실패 아님). 미래 누설 없음.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/daily_shadow.md
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from agents.decision_outcome import HORIZONS


def record_id(rec) -> str:
    """안정 레코드 ID = date|symbol|decision."""
    return f"{rec.get('date')}|{rec.get('symbol')}|{rec.get('decision')}"


def merge_decision_ledger(existing, new):
    """결정 원장에 ID 미존재 행만 append(dedupe). (merged_list, added_count)."""
    seen = {record_id(r) for r in existing}
    merged = list(existing)
    added = 0
    for r in new:
        rid = record_id(r)
        if rid in seen:
            continue
        seen.add(rid)
        r = {**r, "record_id": rid}
        merged.append(r)
        added += 1
    return merged, added


def upsert_outcome_ledger(existing, new):
    """결과 원장을 ID로 upsert(성숙 갱신). 최신 new가 기존을 덮는다."""
    by_id = {record_id(r): {**r, "record_id": record_id(r)} for r in existing}
    for r in new:
        rid = record_id(r)
        by_id[rid] = {**r, "record_id": rid}
    return sorted(by_id.values(), key=lambda r: (str(r.get("date")), str(r.get("symbol"))))


def _ret(outcome, h):
    """ScoredRecord.outcome 또는 dict outcome에서 horizon return."""
    returns = getattr(outcome, "returns", None)
    if returns is None and isinstance(outcome, dict):
        returns = outcome.get("returns", {})
    if returns is None:
        return None
    return returns.get(h, returns.get(str(h)))


def _scorable(s):
    return getattr(s.outcome, "scorable", False)


def count_matured(scored, *, horizons=HORIZONS, decision=None):
    """horizon별 matured(값 있음) 레코드 수."""
    rows = [s for s in scored if _scorable(s) and (decision is None or s.decision == decision)]
    return {h: sum(1 for s in rows if _ret(s.outcome, h) is not None) for h in horizons}


def count_pending(scored, *, horizons=HORIZONS, decision=None):
    """horizon별 pending(scorable이지만 아직 값 없음) 레코드 수."""
    rows = [s for s in scored if _scorable(s) and (decision is None or s.decision == decision)]
    return {h: sum(1 for s in rows if _ret(s.outcome, h) is None) for h in horizons}


def count_newly_matured(existing_by_id, scored, *, horizons=HORIZONS):
    """이번 실행에서 처음 값이 생긴 (id, horizon) 수(기존 결과 원장 대비)."""
    out = {h: 0 for h in horizons}
    for s in scored:
        if not _scorable(s):
            continue
        rid = f"{s.date}|{s.symbol}|{s.decision}"
        prev = existing_by_id.get(rid)
        prev_returns = (prev.get("outcome", {}) or {}).get("returns", {}) if prev else {}
        for h in horizons:
            now = _ret(s.outcome, h)
            was = prev_returns.get(str(h), prev_returns.get(h))
            if now is not None and was is None:
                out[h] += 1
    return out


@dataclass(frozen=True)
class DailyShadowReport:
    date: str
    n_buy: int
    n_reject: int
    n_skip: int
    riskgate_vetoes: int
    buys: tuple                    # 오늘 BUY DecisionRecord
    top_reject_reasons: tuple
    top_skip_reasons: tuple
    matured_counts: dict
    pending_counts: dict
    newly_matured: dict
    avg_matured_return: dict       # BUY matured 평균 return per horizon
    reentry_total: int
    reentry_count: int
    top1_symbol: str | None
    top1_share: float | None
    top3_share: float | None
    ledger_total: int
    health_status: str = "PASS"
    health_warnings: tuple = field(default_factory=tuple)
    warnings: tuple = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def _top_reasons(records, decision, *, n=5):
    counts: dict[str, int] = {}
    for r in records:
        if r.decision != decision:
            continue
        key = (r.reason or "").split("|")[0].strip() or "(none)"
        counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n])


def build_daily_shadow(date, today_records, scored, existing_outcomes_by_id, *, buy_summary,
                       horizons=HORIZONS, health_status="PASS", health_warnings=()) -> DailyShadowReport:
    """오늘 결정 + 전체 원장 채점으로 일간 섀도 리포트를 만든다."""
    today_records = tuple(today_records)
    n_buy = sum(1 for r in today_records if r.decision == "BUY")
    n_reject = sum(1 for r in today_records if r.decision == "REJECT")
    n_skip = sum(1 for r in today_records if r.decision == "SKIP")
    vetoes = sum(1 for r in today_records if r.decision == "REJECT" and r.riskgate_passed is False)
    buys = tuple(r for r in today_records if r.decision == "BUY")

    matured = count_matured(scored, horizons=horizons, decision="BUY")
    pending = count_pending(scored, horizons=horizons, decision="BUY")
    newly = count_newly_matured(existing_outcomes_by_id, scored, horizons=horizons)

    reentry_rows = [s for s in scored if getattr(s.reentry, "available", False)]
    reentry_count = sum(1 for s in reentry_rows if getattr(s.reentry, "is_reentry", False))

    warnings: list[str] = []
    if buy_summary.top1_share is not None and buy_summary.top1_share > 0.35:
        warnings.append(f"BUY 60d 양수 수익이 {buy_summary.top1_symbol}에 "
                        f"{buy_summary.top1_share:.0%} 집중 — 소수 종목 의존")
    warnings.append("in-sample/단일 강세 구간 누적 — 전진 증거는 라이브 일별 누적으로만. 베이스라인 변경 없음.")

    return DailyShadowReport(
        date=date, n_buy=n_buy, n_reject=n_reject, n_skip=n_skip, riskgate_vetoes=vetoes, buys=buys,
        top_reject_reasons=_top_reasons(today_records, "REJECT"),
        top_skip_reasons=_top_reasons(today_records, "SKIP"),
        matured_counts=matured, pending_counts=pending, newly_matured=newly,
        avg_matured_return={h: buy_summary.avg_returns.get(h) for h in horizons},
        reentry_total=len(reentry_rows), reentry_count=reentry_count,
        top1_symbol=buy_summary.top1_symbol, top1_share=buy_summary.top1_share,
        top3_share=buy_summary.top3_share, ledger_total=len(tuple(scored)),
        health_status=health_status, health_warnings=tuple(health_warnings), warnings=tuple(warnings),
    )


def _pct(value, fmt="{:.2%}") -> str:
    return "n/a" if value is None else fmt.format(value)


def format_daily_shadow_markdown(report: DailyShadowReport) -> str:
    """사람이 읽는 일간 섀도 리포트(측정 보조 — 매매 미사용)."""
    lines: list[str] = []
    lines.append(f"# Daily Shadow Report — {report.date} (측정 - 실주문 없음, 진입 next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 결정 누적 + 성숙한 결과 "
                 "채점 — 스캐너/디시전/사이징/RiskGate·진입/청산/유니버스·베이스라인 미변경. forward만 사용.")
    lines.append("")
    badge = {"PASS": "✅ PASS", "WARN": "⚠️ WARN", "FAIL": "❌ FAIL"}.get(report.health_status, report.health_status)
    lines.append(f"**데이터 헬스: {badge}**" +
                 (" — " + "; ".join(report.health_warnings) if report.health_warnings else ""))
    lines.append("")
    lines.append(f"**오늘 결정**: BUY {report.n_buy} · REJECT {report.n_reject} · SKIP {report.n_skip} · "
                 f"RiskGate veto {report.riskgate_vetoes} · 원장 누적 {report.ledger_total} · real_orders 0")
    lines.append("")

    lines.append("## 오늘 BUY (planned entry/exit — report-only)")
    lines.append("")
    if report.buys:
        lines.append("| symbol | entry | exit | held |")
        lines.append("|---|---|---|---|")
        for r in report.buys:
            lines.append(f"| {r.symbol} | {r.planned_entry_type} (buf {r.entry_limit_buffer_pct:.0%}) | "
                         f"stop {r.planned_stop_loss:.0%}/trail {r.planned_trailing_stop:.0%}/"
                         f"{r.planned_max_holding}d | {r.position_shares:.4f} |")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("## REJECT / SKIP 상위 사유")
    lines.append("")
    lines.append("- REJECT: " + (", ".join(f"{r}×{c}" for r, c in report.top_reject_reasons) or "없음"))
    lines.append("- SKIP: " + (", ".join(f"{r}×{c}" for r, c in report.top_skip_reasons) or "없음"))
    lines.append("")

    lines.append("## 결과 성숙 (BUY 원장)")
    lines.append("")
    lines.append("| horizon | newly matured | matured total | pending | avg return(matured) |")
    lines.append("|---|---|---|---|---|")
    for h in HORIZONS:
        lines.append(f"| {h}d | {report.newly_matured.get(h, 0)} | {report.matured_counts.get(h, 0)} | "
                     f"{report.pending_counts.get(h, 0)} | {_pct(report.avg_matured_return.get(h))} |")
    lines.append("")

    lines.append(f"## 재진입 요약: 원장 {report.reentry_total}건 중 재진입 {report.reentry_count}건 "
                 f"{'(컨텍스트 있음)' if report.reentry_total else '(컨텍스트 없음)'}")
    lines.append("")
    lines.append(f"- BUY 60d 양수 수익 집중: top {report.top1_symbol or '-'} {_pct(report.top1_share, '{:.0%}')}, "
                 f"top3 {_pct(report.top3_share, '{:.0%}')}")
    lines.append("")

    if report.warnings:
        lines.append("## 경고")
        lines.append("")
        for w in report.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")
    lines.append(f"**주문 미실행 확인**: `real_orders_placed = {report.real_orders_placed}`.")
    lines.append("")
    return "\n".join(lines)
