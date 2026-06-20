"""주문 전 검증 + 시뮬레이션 실행 경로 — 자동매매로 가는 시뮬 단계.

후보(VetoInput + 제안 Decision)를 받아 ① 전역 게이트(kill-switch) ② per-candidate hard-veto를 모두
통과 + 진입(BUY)일 때만 시뮬레이트 주문을 만든다. 실브로커·Robinhood·MCP·라이브 주문 없음.

CRITICAL (RiskGate 우회 불가): 시뮬 주문 생성·기록의 유일한 경로는 submit()이며, submit은 항상 두 게이트를
평가한다. 주문 리스트는 읽기전용 뷰로만 노출 — 직접 append하는 공개 경로 없음. veto된 후보는 effective가
BUY가 될 수 없어(dry_run의 RiskGate 최종권) 반드시 거부된다.

CRITICAL (실주문 0 불변): OrderProvider·place_order·브로커를 부르지 않는다. real_orders_placed는 항상 0.

CRITICAL: agents/executor.py(라이브 경로)를 건드리지 않는다. 이 모듈은 별도의 시뮬 경로이며, 기존
전역 게이트 check_risk_gate와 hard-veto를 재사용할 뿐 새 게이트를 만들지 않는다.

spec: specs/sim_execution.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agents.decision import Decision
from agents.dry_run import build_dry_run_decision
from agents.risk import check_risk_gate
from algorithms.policy import VetoInput, VetoResult

# 전역 게이트 시그니처: () -> (allowed, reason). 기본은 agents.risk.check_risk_gate(env kill-switch).
GlobalGate = Callable[[], "tuple[bool, str]"]


@dataclass(frozen=True)
class SimulatedOrder:
    """시뮬레이트 주문 기록. 어떤 브로커로도 전송되지 않는다."""

    symbol: str
    side: str  # "buy" — 이번 범위는 진입 시뮬만
    quantity: int
    note: str = "SIMULATED — no broker / no live order"


@dataclass(frozen=True)
class SimExecutionResult:
    """submit 결과. created=False면 order None + 거부 사유."""

    created: bool
    order: SimulatedOrder | None
    veto: VetoResult | None
    reason: str


class SimulatedExecutor:
    """RiskGate를 통과한 후보만 시뮬 주문으로 만든다. 실주문은 절대 발생하지 않는다.

    게이트(전역 kill-switch + per-candidate hard-veto)는 재사용한다 — 새 게이트를 만들지 않는다.
    """

    def __init__(self, *, global_gate: GlobalGate = check_risk_gate) -> None:
        self._global_gate = global_gate
        self._orders: list[SimulatedOrder] = []
        self.rejections: list[str] = []

    @property
    def simulated_orders(self) -> tuple[SimulatedOrder, ...]:
        """기록된 시뮬 주문(읽기전용 뷰). 외부에서 직접 추가 불가."""
        return tuple(self._orders)

    @property
    def real_orders_placed(self) -> int:
        """항상 0 — 실 브로커 호출 없음(구조적 불변식)."""
        return 0

    def submit(
        self, veto_input: VetoInput, raw_decision: Decision, quantity: int
    ) -> SimExecutionResult:
        """후보를 검증해 통과 시에만 시뮬 주문을 만든다(spec 순서 — 우회 불가)."""
        # ① per-candidate hard-veto 평가(RiskGate 최종권 포함). 예외 → fail-closed.
        try:
            row = build_dry_run_decision(veto_input, raw_decision)
        except Exception as exc:  # noqa: BLE001 — 평가 실패 시 안전하게 거부.
            return self._reject(None, f"hard-veto 평가 예외 → fail-closed 거부: {exc}")
        veto = row.veto

        # ② 수량 검증.
        if quantity <= 0:
            return self._reject(veto, f"수량 {quantity} <= 0 — 시뮬 주문 없음")

        # ③ 전역 게이트(kill-switch). 예외/차단 → fail-closed 거부.
        try:
            allowed, reason = self._global_gate()
        except Exception as exc:  # noqa: BLE001 — 게이트 평가 실패 시 안전하게 거부.
            return self._reject(veto, f"전역 게이트 예외 → fail-closed 거부: {exc}")
        if not allowed:
            return self._reject(veto, f"전역 게이트 차단 — 시뮬 주문 없음: {reason}")

        # ④ RiskGate 최종권: effective BUY가 아니면 거부. (BUY ⟺ veto 통과 AND raw BUY)
        if row.effective_decision is not Decision.BUY:
            if not veto.passed:
                return self._reject(
                    veto, "RiskGate veto — 시뮬 주문 없음: " + "; ".join(veto.reasons)
                )
            return self._reject(
                veto, f"진입(BUY) 아님(raw={raw_decision.value}) — 시뮬 주문 없음"
            )

        # ⑤ 통과 — 여기서만 시뮬 주문을 만든다(실주문 아님).
        order = SimulatedOrder(symbol=veto_input.symbol, side="buy", quantity=quantity)
        self._orders.append(order)
        return SimExecutionResult(
            created=True,
            order=order,
            veto=veto,
            reason="RiskGate PASS — 시뮬 주문 생성(실주문 아님, real_orders_placed=0)",
        )

    def _reject(self, veto: VetoResult | None, reason: str) -> SimExecutionResult:
        """시뮬 주문을 만들지 않고 사유를 기록한다."""
        self.rejections.append(reason)
        return SimExecutionResult(created=False, order=None, veto=veto, reason=reason)
