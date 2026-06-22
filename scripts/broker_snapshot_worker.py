#!/usr/bin/env python3
"""Broker 스냅샷 워커 (read-only 브리지 v0) — Claude/Codex MCP 워커 entrypoint.

배경(reports/fastapi_mcp_feasibility.md): FastAPI 백엔드는 Robinhood MCP에 직접 붙을 수 없다
(최초 인가가 대화형 OAuth). 그래서 v0에서는 **Claude Code/Codex가 MCP 워커**가 되어 읽기 전용
도구를 호출하고, 그 원본을 이 스크립트로 살균해 `reports/broker_snapshots.jsonl`에 적재한다.

이 스크립트 자체는 MCP/네트워크/인증을 수행하지 않는다(못 한다). 워커(사람이 띄운 Claude/Codex)가:
  1) control_flags.json을 먼저 확인하고(어떤 액션 전에도),
  2) Robinhood 읽기 전용 도구만 호출한다:
       get_accounts, get_portfolio, get_equity_positions,
       get_equity_orders(state=new), get_equity_quotes
  3) 응답을 아래 형식의 JSON으로 모아 이 스크립트에 넘긴다(--from-json / --from-stdin).

CRITICAL 안전:
  - 주문/취소/리뷰/쓰기 MCP 도구 호출 금지. real_orders_placed는 항상 0.
  - MCP 미가용이면 **명확히 실패**한다(exit!=0). 가짜 데이터를 만들지 않는다.
  - 계정번호는 마지막 4자리만 저장된다(빌더가 마스킹). 토큰/시크릿 미저장.

입력 JSON 형식(원본 MCP 응답 그대로 넣으면 된다):
{
  "provider": "robinhood-mcp",
  "source": "claude-code-worker",
  "accounts":    <get_accounts 응답>,
  "portfolio":   <get_portfolio 응답>,
  "positions":   <get_equity_positions 응답>,
  "open_orders": <get_equity_orders(state=new) 응답>,
  "quotes":      <get_equity_quotes 응답>
}

사용:
  python -m scripts.broker_snapshot_worker --from-json raw.json
  cat raw.json | python -m scripts.broker_snapshot_worker --from-stdin
  python -m scripts.broker_snapshot_worker --show-flags   # 현재 control_flags만 출력
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.app.services.broker_snapshot import append_snapshot, build_snapshot_from_raw
from backend.app.services.control_flags import read_control_flags

_RUNBOOK = (
    "MCP는 이 프로세스에서 직접 호출할 수 없다. Claude Code/Codex(MCP 워커)에서 읽기 전용 도구\n"
    "(get_accounts, get_portfolio, get_equity_positions, get_equity_orders[state=new],\n"
    "get_equity_quotes)를 호출해 원본을 JSON으로 모은 뒤 --from-json/--from-stdin 으로 넘겨라.\n"
    "데이터를 위조하지 말 것."
)


def _load_raw(args: argparse.Namespace) -> dict:
    if args.from_stdin:
        return json.loads(sys.stdin.read())
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            return json.load(fh)
    raise SystemExit(f"입력이 없다(MCP 미가용). 가짜 스냅샷을 만들지 않는다.\n\n{_RUNBOOK}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Broker 스냅샷 워커(read-only).")
    parser.add_argument("--from-json", help="원본 MCP 응답 JSON 파일 경로")
    parser.add_argument("--from-stdin", action="store_true", help="원본 MCP 응답 JSON을 stdin에서 읽기")
    parser.add_argument("--show-flags", action="store_true", help="현재 control_flags만 출력하고 종료")
    args = parser.parse_args(argv)

    # 워커는 어떤 액션 전에도 control_flags를 먼저 확인한다(스냅샷은 읽기 전용이라 차단 대상은
    # 아니지만, 가시성을 위해 항상 출력한다). None이면 fail-closed로 간주해야 한다.
    flags = read_control_flags()
    print(f"control_flags: {flags.model_dump() if flags else '(없음 → fail-closed로 간주)'}", file=sys.stderr)
    if args.show_flags:
        return 0

    raw = _load_raw(args)
    snapshot = build_snapshot_from_raw(
        raw,
        provider=raw.get("provider", "robinhood-mcp"),
        source=raw.get("source", "claude-code-worker"),
    )
    written = append_snapshot(snapshot)
    # 살균된 스냅샷만 출력(전체 계정번호/토큰 없음). real_orders_placed=0 확인.
    print(json.dumps(written.model_dump(), ensure_ascii=False, indent=2))
    if written.errors:
        print(f"경고: 스냅샷에 errors {len(written.errors)}건 — {written.errors}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
