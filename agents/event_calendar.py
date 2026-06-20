"""이벤트 캘린더 CSV provider — 로컬 events.csv 기반 이벤트 리스크 확인(수동, 라이브 API 아님).

기존 EventRiskProvider 인터페이스(is_clear)를 구현해 historical_sim/run_sim에 꽂힌다. high severity
이벤트만 진입을 차단한다(event_risk_checked=False → 기존 hard-veto). 차단은 RiskGate 불리언 게이트와
맞물릴 뿐, 전략/리스크 규칙을 바꾸지 않는다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. LLM 미연결. 라이브
이벤트 API 미연결. 전략 시그널 튜닝 없음. CSV provider만.

CRITICAL (fail-closed, 가정 금지): 필수 컬럼 누락/날짜 무효 → EventCalendarError. as_of 모르면(None)
is_clear=False(확인 불가 → 안전).

spec: specs/event_calendar.md
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MARKET = "MARKET"  # 전 심볼에 적용되는 시장 이벤트 ticker(FOMC/CPI 등).
_REQUIRED = ("date", "event_type", "ticker", "severity", "notes")


class EventCalendarError(Exception):
    """events.csv 로드/검증 실패(fail-closed)."""


@dataclass(frozen=True)
class CalendarEvent:
    """이벤트 1건."""

    date: pd.Timestamp
    event_type: str
    ticker: str
    severity: str
    notes: str

    def applies_to(self, symbol: str) -> bool:
        """이 이벤트가 해당 심볼에 적용되는가(해당 ticker 또는 MARKET)."""
        return self.ticker == MARKET or self.ticker == symbol


class EventCalendarProvider:
    """로컬 events.csv 기반 이벤트 리스크 provider. 기본적으로 high severity만 차단한다."""

    def __init__(
        self,
        events,
        *,
        block_severities=("high",),
        window_days: int = 0,
    ) -> None:
        self._block = {str(s).strip().lower() for s in block_severities}
        self._window_days = max(0, int(window_days))
        # 날짜별 인덱스(빠른 조회). 차단 severity 이벤트만 보관.
        self._by_date: dict[pd.Timestamp, list[CalendarEvent]] = {}
        for ev in events:
            if ev.severity.strip().lower() in self._block:
                self._by_date.setdefault(ev.date, []).append(ev)

    # --- 로드 ---

    @classmethod
    def from_frame(cls, df: pd.DataFrame, **kwargs) -> "EventCalendarProvider":
        """DataFrame을 검증해 provider를 만든다(fail-closed)."""
        lowered = {str(c).strip().lower(): c for c in df.columns}
        missing = [c for c in _REQUIRED if c not in lowered]
        if missing:
            raise EventCalendarError(
                f"events.csv 필수 컬럼 누락: {missing} (있는 컬럼: {list(df.columns)}) — 컬럼 추정 안 함"
            )

        dates = pd.to_datetime(df[lowered["date"]], errors="coerce")
        if dates.isna().any():
            bad = df.loc[dates.isna(), lowered["date"]].tolist()
            raise EventCalendarError(f"events.csv 날짜 무효(파싱 실패): {bad}")

        events: list[CalendarEvent] = []
        for i, (_, row) in enumerate(df.iterrows()):
            ticker = str(row[lowered["ticker"]]).strip()
            severity = str(row[lowered["severity"]]).strip()
            if not ticker or not severity:
                raise EventCalendarError(f"events.csv {i}행: ticker/severity 비어 있음")
            events.append(CalendarEvent(
                date=dates.iloc[i].normalize(),
                event_type=str(row[lowered["event_type"]]).strip(),
                ticker=ticker,
                severity=severity,
                notes=str(row[lowered["notes"]]),
            ))
        return cls(events, **kwargs)

    @classmethod
    def from_csv(cls, path, **kwargs) -> "EventCalendarProvider":
        """CSV 파일을 읽어 provider를 만든다. 파일 없음/파싱 실패 → EventCalendarError."""
        try:
            df = pd.read_csv(path)
        except FileNotFoundError as exc:
            raise EventCalendarError(f"events.csv 파일 없음: {path}") from exc
        except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise EventCalendarError(f"events.csv 읽기 실패: {path} ({exc})") from exc
        return cls.from_frame(df, **kwargs)

    # --- 조회 ---

    def events_on(self, symbol: str, as_of) -> tuple[CalendarEvent, ...]:
        """as_of 윈도우에서 symbol(또는 MARKET)에 적용되는 차단 이벤트들(진단/증거용)."""
        if as_of is None:
            return ()
        d = pd.Timestamp(as_of).normalize()
        hits: list[CalendarEvent] = []
        for offset in range(self._window_days + 1):
            day = d + pd.Timedelta(days=offset)
            for ev in self._by_date.get(day, ()):
                if ev.applies_to(symbol):
                    hits.append(ev)
        return tuple(hits)

    def is_clear(self, symbol: str, as_of=None) -> bool:
        """as_of에 symbol/MARKET 대상 차단 이벤트가 없으면 True(clear). as_of=None → False(fail-closed)."""
        if as_of is None:
            return False  # 날짜 불명 → 이벤트 확인 불가 → 안전하게 미확인 처리.
        return len(self.events_on(symbol, as_of)) == 0
