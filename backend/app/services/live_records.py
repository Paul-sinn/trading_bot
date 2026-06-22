"""라이브 거래 기록 — 일간 jsonl 저장 + 주간 집계(순수).

`reports/live_daily_records.jsonl`(date 멱등 upsert) / `reports/live_sessions.jsonl`(이벤트 append).
**기록 생성은 절대 주문하지 않는다.** Shadow 산출물과 물리적으로 분리된 live 전용 파일만 다룬다.
파일 부재/손상은 빈 상태로 안전 처리(크래시 없음). `reports/`는 .gitignore라 커밋되지 않는다.

spec: specs/live_session.md
"""

from __future__ import annotations

import json
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"

DAILY_LOG = "live_daily_records.jsonl"
SESSIONS_LOG = "live_sessions.jsonl"


class LiveDailyRecord(BaseModel):
    """하루치 라이브 거래 기록. MCP 미연동 시 주문/pnl은 0/None."""

    date: str
    session_ids: list[str] = Field(default_factory=list)
    started_at: str | None = None
    stopped_at: str | None = None
    orders_submitted: int = 0
    orders_filled: int = 0
    orders_cancelled: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float | None = None
    win_rate: float | None = None
    max_drawdown_intraday: float | None = None
    stop_reason: str | None = None
    notes: str = ""
    real_orders_placed: int = 0  # 불변식: 항상 0


class LiveWeeklyRecord(BaseModel):
    """일간 기록에서 집계한 주간 요약(월요일 시작 주)."""

    week_start: str
    week_end: str
    trading_days: int = 0
    total_orders: int = 0
    filled_orders: int = 0
    realized_pnl: float = 0.0
    win_rate: float | None = None
    max_daily_loss: float = 0.0
    notes: str = ""


def _daily_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / DAILY_LOG


def _append_jsonl(path: Path, record: dict) -> None:
    """reports/ 디렉토리를 생성한 뒤 한 줄짜리 JSON 레코드를 append한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_session_event(event: dict, *, reports_dir: Path | None = None) -> None:
    """세션 이벤트(start/stop/emergency)를 `live_sessions.jsonl`에 append한다(주문 아님)."""
    _append_jsonl((reports_dir or DEFAULT_REPORTS_DIR) / SESSIONS_LOG, event)


def load_daily_records(*, reports_dir: Path | None = None) -> list[LiveDailyRecord]:
    """일간 기록을 읽는다. 부재/손상 라인은 건너뛰고 빈 리스트까지 안전 처리."""
    path = _daily_path(reports_dir)
    if not path.exists():
        return []
    # date 멱등: 같은 date가 여러 번 append됐으면 마지막 것이 유효(upsert 의미).
    by_date: dict[str, LiveDailyRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = LiveDailyRecord.model_validate_json(line)
        except (ValueError, TypeError):
            continue  # 손상 라인 무시(크래시 없음)
        by_date[rec.date] = rec
    return [by_date[d] for d in sorted(by_date)]


def upsert_daily_record(record: LiveDailyRecord, *, reports_dir: Path | None = None) -> LiveDailyRecord:
    """일간 기록을 date 멱등으로 갱신해 저장한다.

    같은 date의 기존 행이 있으면 병합(세션 id 합집합, 시간/사유 갱신)한 뒤 새 행을 append한다.
    `load_daily_records`가 같은 date의 마지막 행만 취하므로 결과적으로 upsert가 된다.
    **이 함수는 주문을 내지 않는다 — 파일 쓰기만.**
    """
    existing = {r.date: r for r in load_daily_records(reports_dir=reports_dir)}
    prior = existing.get(record.date)
    if prior is not None:
        merged_ids = list(dict.fromkeys([*prior.session_ids, *record.session_ids]))
        record = record.model_copy(update={
            "session_ids": merged_ids,
            "started_at": prior.started_at or record.started_at,
            "orders_submitted": prior.orders_submitted + record.orders_submitted,
            "orders_filled": prior.orders_filled + record.orders_filled,
            "orders_cancelled": prior.orders_cancelled + record.orders_cancelled,
        })
    record = record.model_copy(update={"real_orders_placed": 0})  # 불변식 강제
    _append_jsonl(_daily_path(reports_dir), record.model_dump())
    return record


def _iso_monday(d: _date) -> _date:
    return d - timedelta(days=d.weekday())


def aggregate_weekly(daily_records: list[LiveDailyRecord]) -> list[LiveWeeklyRecord]:
    """일간 기록을 주(월~일) 단위로 집계한다(순수 함수 — I/O 없음)."""
    weeks: dict[str, list[LiveDailyRecord]] = {}
    for rec in daily_records:
        try:
            d = datetime.strptime(rec.date, "%Y-%m-%d").date()
        except ValueError:
            continue
        key = _iso_monday(d).isoformat()
        weeks.setdefault(key, []).append(rec)

    out: list[LiveWeeklyRecord] = []
    for week_start in sorted(weeks):
        items = weeks[week_start]
        days = sorted(r.date for r in items)
        filled = sum(r.orders_filled for r in items)
        total_orders = sum(r.orders_submitted for r in items)
        realized = sum(r.realized_pnl for r in items)
        # 최대 일손실(가장 음수인 일간 realized_pnl). 손실 없으면 0.
        max_daily_loss = min([r.realized_pnl for r in items] + [0.0])
        wr_vals = [r.win_rate for r in items if r.win_rate is not None]
        win_rate = (sum(wr_vals) / len(wr_vals)) if wr_vals else None
        out.append(LiveWeeklyRecord(
            week_start=week_start,
            week_end=days[-1] if days else week_start,
            trading_days=len(items),
            total_orders=total_orders,
            filled_orders=filled,
            realized_pnl=realized,
            win_rate=win_rate,
            max_daily_loss=max_daily_loss,
            notes="no broker connected" if total_orders == 0 else "",
        ))
    return out
