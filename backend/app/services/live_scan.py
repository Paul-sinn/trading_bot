"""report_only 라이브 퀀트 스캔 루프 — 베이스라인 유니버스 모니터링(주문/LLM 없음).

MarketDataAdapter로 quote/bar를 받아 잠긴 알고리즘(signals/entry/regime)을 **읽기 전용**으로
재사용해 심볼별 BUY_CANDIDATE/REJECT/SKIP/INSUFFICIENT_DATA/ERROR를 낸다. 이벤트는
`reports/live_scan_events.jsonl`에만 append한다(shadow 파일·Norgate 무관).

CRITICAL 불변식: 실주문 없음(real_orders_placed=0), LLM 호출 없음, 브로커 호출 없음.
잠긴 베이스라인/유니버스/scanner·decision·sizing·RiskGate 로직 미변경(여기서 호출만).

spec: specs/live_scan.md
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from algorithms.entry import pullback_entry
from algorithms.regime import Regime
from algorithms.signals import relative_strength, rsi_value, trend_state
from backend.app.services.market_data import (
    SPY_SYMBOL,
    MarketDataProvider,
)
from backend.app.services.regime_adapter import RegimeDataAdapter, RegimeResult

# 베이스라인 유니버스 — experiments.universe_bias_test.BASELINE_UNIVERSE의 미러.
# (그 모듈은 import 부작용이 커서 Live를 리서치/섀도 러너와 분리하기 위해 상수만 복제한다.
#  tests/test_live_scan.py의 드리프트 가드가 동일성을 강제한다.)
LIVE_BASELINE_UNIVERSE: tuple[str, ...] = (
    "NVDA", "AMD", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA",
    "AVGO", "SMCI", "ARM", "MU", "TSM", "ASML", "NFLX", "ORCL", "CRM", "PLTR",
)

SCAN_LOG = "live_scan_events.jsonl"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORTS_DIR = _REPO_ROOT / "reports"

# scan_status 값.
BUY_CANDIDATE = "BUY_CANDIDATE"
REJECT = "REJECT"
SKIP = "SKIP"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
ERROR = "ERROR"

_SLOW_MA = 200  # 추세 판정 워밍업(헌장 §1: 200d). bars < 200 → INSUFFICIENT_DATA.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanEvent(BaseModel):
    """심볼별 스캔 결과(report-only). real_orders_placed 불변식상 항상 0."""

    timestamp: str
    session_id: str | None = None
    trading_mode: str = "report_only"
    provider: str = ""
    symbol: str
    price: float | None = None
    scan_status: str
    reason: str = ""
    features: dict = Field(default_factory=dict)
    riskgate_status: str | None = None  # report_only에서는 평가 안 함(None)
    buy_candidate: bool = False
    real_orders_placed: int = 0
    # 레짐 어댑터(§18) 메타 — 대시보드/상태용.
    regime_source: str | None = None  # spy+vix | spy_only | none
    vix_value: float | None = None
    risk_reduced: bool = False
    regime_warning: str | None = None


def _scan_path(reports_dir: Path | None) -> Path:
    return (reports_dir or DEFAULT_REPORTS_DIR) / SCAN_LOG


def _classify_reason(reason: str) -> str:
    """entry.pullback_entry의 reason 문자열을 scan_status로 사상(추측 금지)."""
    if reason.startswith("게이트 실패"):
        return REJECT
    if "데이터 부족" in reason or "워밍업" in reason:
        return INSUFFICIENT_DATA
    if reason.startswith("트리거"):
        return SKIP
    return SKIP


class LiveScanLoop:
    """베이스라인 유니버스 1회 스캔(scan_cycle) + jsonl append. 주문/LLM 없음."""

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        reports_dir: Path | None = None,
        universe: tuple[str, ...] = LIVE_BASELINE_UNIVERSE,
        max_symbols: int = 0,
        vix_fetch=None,
    ) -> None:
        self._provider = provider
        self._reports_dir = reports_dir
        self._universe = universe
        self._max_symbols = max_symbols
        # 레짐 어댑터: SPY는 provider, VIX는 폴백(yfinance→stooq). vix_fetch 주입으로 테스트.
        self._regime_adapter = RegimeDataAdapter(provider, vix_fetch=vix_fetch)

    def _symbols_this_cycle(self) -> list[str]:
        syms = list(self._universe)
        if self._max_symbols and self._max_symbols > 0:
            return syms[: self._max_symbols]
        return syms

    def _resolve_regime(self) -> tuple[RegimeResult, Regime | None, "pd.Series | None"]:
        """레짐 어댑터로 레짐 + SPY close 시리즈를 구한다. VIX 없으면 SPY-only 보수 레짐.

        반환: (RegimeResult, pullback용 Regime enum | None, SPY close 시리즈 | None).
        regime이 None인 경우는 **SPY가 실제로 없을 때만** — VIX 부재는 더 이상 전 심볼 skip을 만들지 않는다.
        """
        try:
            spy_bars = self._provider.get_recent_bars(SPY_SYMBOL, lookback_days=300)
        except Exception:  # noqa: BLE001 - graceful
            spy_bars = None
        rr = self._regime_adapter.resolve(spy_bars=spy_bars)
        regime: Regime | None = Regime(rr.effective_regime) if rr.effective_regime else None
        spy_close = (
            spy_bars["close"]
            if spy_bars is not None and "close" in spy_bars.columns and len(spy_bars) >= _SLOW_MA
            else None
        )
        return rr, regime, spy_close

    def scan_cycle(
        self, *, session_id: str | None, trading_mode: str = "report_only"
    ) -> list[ScanEvent]:
        """베이스라인 유니버스 1회 스캔. 이벤트를 jsonl에 append하고 리스트로 반환한다."""
        provider_name = getattr(self._provider, "name", "unknown")
        rr, regime, spy_close = self._resolve_regime()

        events: list[ScanEvent] = []
        for symbol in self._symbols_this_cycle():
            events.append(
                self._scan_symbol(symbol, regime, spy_close, session_id, trading_mode, provider_name, rr)
            )
        self._append_events(events)
        return events

    def _scan_symbol(
        self,
        symbol: str,
        regime: Regime | None,
        spy_close: "pd.Series | None",
        session_id: str | None,
        trading_mode: str,
        provider_name: str,
        rr: RegimeResult,
    ) -> ScanEvent:
        warning = rr.warnings[0] if rr.warnings else None

        def event(
            scan_status: str,
            reason: str,
            *,
            price: float | None = None,
            features: dict | None = None,
            buy_candidate: bool = False,
        ) -> ScanEvent:
            feat = features or {}
            feat.setdefault("regime", rr.regime)
            feat.setdefault("regime_source", rr.regime_source)
            return ScanEvent(
                timestamp=_now_iso(),
                session_id=session_id,
                trading_mode=trading_mode,
                provider=provider_name,
                symbol=symbol,
                price=price,
                scan_status=scan_status,
                reason=reason,
                features=feat,
                buy_candidate=buy_candidate,
                regime_source=rr.regime_source,
                vix_value=rr.vix_value,
                risk_reduced=rr.risk_reduced,
                regime_warning=warning,
            )

        try:
            bars = self._provider.get_recent_bars(symbol, lookback_days=300)
        except Exception as exc:  # noqa: BLE001 - graceful: provider 실패 → ERROR
            return event(ERROR, f"데이터 조회 실패: {exc}")

        if "close" not in bars.columns or len(bars) < _SLOW_MA:
            return event(INSUFFICIENT_DATA, "bars < 200(추세 워밍업 전)")
        # 레짐 None은 **SPY가 실제로 없을 때만**(VIX 부재는 SPY-only 레짐으로 처리됨).
        if regime is None or spy_close is None:
            return event(INSUFFICIENT_DATA, "SPY 데이터 부족 — 레짐 판정 불가")

        close = bars["close"]
        price = float(close.iloc[-1])
        spy_df = spy_close.to_frame(name="close")
        features = {
            "trend": trend_state(close).value,
            "relative_strength": relative_strength(close, spy_close),
            "rsi": rsi_value(close),
            "regime": rr.regime,  # 레짐 라벨(예: spy_bull_vix_unknown 포함)
            "effective_regime": regime.value,
            "regime_source": rr.regime_source,
            "price": price,
        }

        try:
            signal = pullback_entry(bars, regime=regime, spy_df=spy_df)
        except Exception as exc:  # noqa: BLE001 - graceful
            return event(ERROR, f"스캔 오류: {exc}", price=price, features=features)

        status = BUY_CANDIDATE if signal.enter else _classify_reason(signal.reason)
        return event(
            status,
            signal.reason,
            price=price,
            features=features,
            buy_candidate=(status == BUY_CANDIDATE),
        )

    def _append_events(self, events: list[ScanEvent]) -> None:
        if not events:
            return
        path = _scan_path(self._reports_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev.model_dump(), ensure_ascii=False) + "\n")


class RegimeStatus(BaseModel):
    """최신 스캔의 레짐 요약(읽기 전용 — 대시보드용). 주문/네트워크 없음."""

    regime: str | None = None
    regime_source: str | None = None
    vix_value: float | None = None
    risk_reduced: bool = False
    warning: str | None = None
    as_of: str | None = None


def regime_status(*, reports_dir: Path | None = None) -> RegimeStatus:
    """가장 최근 스캔 이벤트에서 레짐 정보를 읽어 요약한다(스캔 시작 안 함)."""
    evs = load_scan_events(limit=1, reports_dir=reports_dir)
    if not evs:
        return RegimeStatus()
    e = evs[-1]
    label = e.features.get("regime") if isinstance(e.features, dict) else None
    return RegimeStatus(
        regime=label, regime_source=e.regime_source, vix_value=e.vix_value,
        risk_reduced=e.risk_reduced, warning=e.regime_warning, as_of=e.timestamp,
    )


def load_scan_events(*, limit: int = 50, reports_dir: Path | None = None) -> list[ScanEvent]:
    """최근 스캔 이벤트 tail(읽기 전용 — 스캔 시작 안 함). 부재/손상 라인은 안전 처리."""
    limit = max(1, min(int(limit), 500))
    path = _scan_path(reports_dir)
    if not path.exists():
        return []
    out: list[ScanEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(ScanEvent.model_validate_json(line))
        except (ValueError, TypeError):
            continue
    return out[-limit:]
