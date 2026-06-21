"""시그널 결정 로그 / 섀도 트레이딩 기반 — 매일 BUY/REJECT/SKIP을 사람·기계가 읽게 기록(순수 측정).

레코드 조립·마크다운·JSONL은 순수 함수. 결정 자체는 기존 dry-run 산출물(DryRunReport.decisions)에서
읽어 변환만 한다 — 스캐너/디시전/사이징/RiskGate/베이스라인을 바꾸지 않는다. planned entry/exit는 잠긴
베이스라인을 '서술'할 뿐(변경 아님). 전진 검증을 위해 JSONL은 append-friendly.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 라이브 전략 시그널
튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결. 리포트/실험 전용 — 동작 변경 없음(읽기만).

spec: specs/decision_log.md
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# 잠긴 베이스라인을 '서술'하는 plan 상수(변경 아님 — 로그 기록용).
PLANNED_ENTRY_TYPE = "next-bar-limit"
PLANNED_ENTRY_LIMIT_BUFFER_PCT = 0.03
PLANNED_STOP_LOSS = 0.15
PLANNED_TRAILING_STOP = 0.20
PLANNED_MAX_HOLDING = 60

DECISIONS = ("BUY", "REJECT", "SKIP")
_SNAP_FIELDS = ("momentum_score", "volume_ratio_20d", "price_above_20ma", "ma20_above_ma50",
                "relative_strength", "distance_from_high")


@dataclass(frozen=True)
class DecisionRecord:
    date: str
    symbol: str
    decision: str                 # BUY | REJECT | SKIP
    reason: str
    momentum_score: float | None
    volume_ratio_20d: float | None
    price_above_20ma: bool | None
    ma20_above_ma50: bool | None
    relative_strength: float | None
    distance_from_high: float | None
    shadow_score: float | None
    riskgate_passed: bool | None
    riskgate_reasons: tuple[str, ...]
    position_shares: float
    planned_entry_type: str = PLANNED_ENTRY_TYPE
    entry_limit_buffer_pct: float = PLANNED_ENTRY_LIMIT_BUFFER_PCT
    planned_stop_loss: float = PLANNED_STOP_LOSS
    planned_trailing_stop: float = PLANNED_TRAILING_STOP
    planned_max_holding: int = PLANNED_MAX_HOLDING
    real_orders_placed: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["riskgate_reasons"] = list(self.riskgate_reasons)
        return d


@dataclass(frozen=True)
class DecisionLog:
    date: str
    records: tuple[DecisionRecord, ...]
    n_buy: int
    n_reject: int
    n_skip: int
    riskgate_vetoes: int
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def real_orders_placed(self) -> int:
        return 0


def make_record(date, symbol, decision, *, reason="", snapshot=None, shadow_score=None,
                riskgate_passed=None, riskgate_reasons=(), position_shares=0.0) -> DecisionRecord:
    """결정 1건을 레코드로 조립한다(plan 상수 + real_orders=0 고정). 순수."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    snap = {f: (getattr(snapshot, f, None) if snapshot is not None else None) for f in _SNAP_FIELDS}
    return DecisionRecord(
        date=date, symbol=symbol, decision=decision, reason=reason,
        momentum_score=snap["momentum_score"], volume_ratio_20d=snap["volume_ratio_20d"],
        price_above_20ma=snap["price_above_20ma"], ma20_above_ma50=snap["ma20_above_ma50"],
        relative_strength=snap["relative_strength"], distance_from_high=snap["distance_from_high"],
        shadow_score=shadow_score, riskgate_passed=riskgate_passed,
        riskgate_reasons=tuple(riskgate_reasons), position_shares=float(position_shares),
    )


def build_decision_log(date, records) -> DecisionLog:
    """레코드들을 묶고 카운트한다."""
    records = tuple(records)
    n_buy = sum(1 for r in records if r.decision == "BUY")
    n_reject = sum(1 for r in records if r.decision == "REJECT")
    n_skip = sum(1 for r in records if r.decision == "SKIP")
    vetoes = sum(1 for r in records if r.riskgate_passed is False)
    warnings: list[str] = []
    if n_buy == 0:
        warnings.append("오늘 BUY 후보 없음 — 전부 REJECT/SKIP")
    return DecisionLog(date=date, records=records, n_buy=n_buy, n_reject=n_reject, n_skip=n_skip,
                       riskgate_vetoes=vetoes, warnings=tuple(warnings))


def records_to_jsonl(records) -> str:
    """레코드들을 JSONL 문자열로(한 줄당 레코드 1개, append-friendly)."""
    return "\n".join(json.dumps(r.to_dict(), ensure_ascii=False, sort_keys=True) for r in records)


def _num(value, fmt="{:.3f}") -> str:
    return "n/a" if value is None else (str(value) if isinstance(value, bool) else fmt.format(value))


def format_decision_log_markdown(log: DecisionLog) -> str:
    """사람이 읽는 결정 로그(측정 보조 — 매매 미사용). 6개 질문에 답한다."""
    buys = [r for r in log.records if r.decision == "BUY"]
    rejects = [r for r in log.records if r.decision == "REJECT"]
    skips = [r for r in log.records if r.decision == "SKIP"]
    vetoed = [r for r in rejects if r.riskgate_passed is False]

    lines: list[str] = []
    lines.append(f"# Signal Decision Log — {log.date} (측정 - 실주문 없음, 진입 next-bar-limit 3% 잠금)")
    lines.append("")
    lines.append("> 실험/리포트 전용. 브로커·라이브 주문 없음. `real_orders_placed = 0`. 기존 dry-run 산출물을 "
                 "읽어 기록만 한다 — 스캐너/디시전/사이징/RiskGate·진입/청산/유니버스·베이스라인 미변경. "
                 "planned entry/exit는 잠긴 베이스라인을 서술할 뿐 변경이 아니다.")
    lines.append("")
    lines.append(f"**요약**: BUY {log.n_buy} · REJECT {log.n_reject} · SKIP {log.n_skip} · "
                 f"RiskGate veto {log.riskgate_vetoes} · real_orders_placed 0")
    lines.append("")

    lines.append("## 오늘 어떤 심볼을 살까? (BUY)")
    lines.append("")
    if buys:
        lines.append("| symbol | mom | vol× | >20ma | shadow | held |")
        lines.append("|---|---|---|---|---|---|")
        for r in buys:
            lines.append(f"| {r.symbol} | {_num(r.momentum_score)} | {_num(r.volume_ratio_20d, '{:.2f}')} | "
                         f"{_num(r.price_above_20ma)} | {_num(r.shadow_score)} | {r.position_shares:.4f} |")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("## 어떤 심볼이 거절됐나? 왜? (REJECT)")
    lines.append("")
    if rejects:
        lines.append("| symbol | RiskGate | reason |")
        lines.append("|---|---|---|")
        for r in rejects:
            gate = "VETO" if r.riskgate_passed is False else ("PASS" if r.riskgate_passed else "n/a")
            reason = r.reason + ((" | " + "; ".join(r.riskgate_reasons)) if r.riskgate_reasons else "")
            lines.append(f"| {r.symbol} | {gate} | {reason} |")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("## RiskGate가 무언가 veto했나?")
    lines.append("")
    if vetoed:
        lines.append(f"- 예 — {len(vetoed)}건 veto: " + ", ".join(r.symbol for r in vetoed))
        for r in vetoed:
            lines.append(f"  - {r.symbol}: {'; '.join(r.riskgate_reasons) or r.reason}")
    else:
        lines.append("- RiskGate veto 없음")
    lines.append("")

    lines.append("## 라이브였다면 주문 계획은? (report-only)")
    lines.append("")
    if buys:
        for r in buys:
            lines.append(f"- **{r.symbol}**: 진입 {r.planned_entry_type} (buffer {r.entry_limit_buffer_pct:.0%}), "
                         f"청산 stop {r.planned_stop_loss:.0%} / trailing {r.planned_trailing_stop:.0%} / "
                         f"max_holding {r.planned_max_holding}일 — **실행 안 됨(report-only)**.")
    else:
        lines.append("- BUY 없음 → 주문 계획 없음.")
    lines.append("")

    lines.append("## SKIP (유니버스에 있으나 스캐너 후보 아님)")
    lines.append("")
    lines.append("- " + (", ".join(r.symbol for r in skips) if skips else "없음"))
    lines.append("")

    if log.warnings:
        lines.append("## 경고")
        lines.append("")
        for w in log.warnings:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    lines.append(f"**주문 미실행 확인**: `real_orders_placed = {log.real_orders_placed}` (실 브로커 호출 없음).")
    lines.append("")
    return "\n".join(lines)
