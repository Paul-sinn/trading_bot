"""리스크 에이전트 — kill-switch 게이트 (임시 인터페이스).

ADR-003: 모든 주문은 PreToolUse hook을 통해 이 게이트를 통과해야 한다.
이 step에서는 시그니처와 최소 구현만 제공한다. 실제 실시간 리스크% 계산은
후속 phase의 리스크 에이전트가 채운다.

원칙: 안전 최우선. 판단이 불확실하면 차단(fail-closed)한다.
"""

from __future__ import annotations

import os


def check_risk_gate() -> tuple[bool, str]:
    """리스크 한도를 평가해 주문 허용 여부를 반환한다.

    Returns:
        (allowed, reason): 허용 여부와 사유.

    최소 구현: 환경변수 `RISK_KILL_SWITCH`가 `"on"`이면 차단, 아니면 허용.
    후속 phase에서 실시간 포지션/드로우다운 기반 계산으로 대체된다.
    """
    kill_switch = os.environ.get("RISK_KILL_SWITCH", "").strip().lower()
    if kill_switch == "on":
        return False, "RISK_KILL_SWITCH가 활성화되어 주문이 차단되었습니다."
    return True, "리스크 한도 내 — 주문 허용."
