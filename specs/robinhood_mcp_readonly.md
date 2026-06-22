# Spec: Robinhood MCP 읽기 전용 클라이언트 (직접 FastAPI MCP PoC)

## 목적
FastAPI 백엔드가 Claude Code와 별개로 **직접 MCP 클라이언트**가 되어 Robinhood Trading MCP의
**읽기 전용** 도구만 호출하는 경로의 안전한 스켈레톤. 주문/취소/리뷰 등 write/action 경로는
이 클라이언트에 **존재하지 않는다**(영구 차단). 자세한 조사 근거는
`reports/fastapi_mcp_feasibility.md`.

## 트랜스포트/인증 (조사 결과)
- 트랜스포트: Streamable HTTP MCP, `https://agent.robinhood.com/mcp/trading` (`type:http`).
- 인증: OAuth 2.0 Bearer (RFC 9728 protected-resource + RFC 8414 AS metadata).
  - `authorization_endpoint`: `https://robinhood.com/oauth` (대화형 브라우저 로그인 + MFA)
  - `token_endpoint`: `https://api.robinhood.com/oauth2/token/`
  - `registration_endpoint`: `https://agent.robinhood.com/oauth/trading/register` (DCR 지원)
  - PKCE S256, grant `authorization_code`+`refresh_token`, public client(`auth_methods=none`).
- 결론: 최초 인가가 대화형이라 **이 태스크에서는 실제 인증/네트워크를 수행하지 않는다.**

## 인터페이스
`RobinhoodMcpReadOnlyClient(enabled=False, settings=None, transport=None, reports_dir=None)`

| 메서드 | 동작 |
|---|---|
| `check_availability()` | `enabled AND transport is not None`일 때만 True (fail-closed) |
| `list_tools()` | 읽기 전용 도구 목록만 반환 (write 도구 절대 미포함) |
| `get_accounts()` | `get_accounts` 호출 |
| `get_portfolio(account_number)` | `get_portfolio` 호출 |
| `get_positions(account_number)` | `get_equity_positions` 호출 |
| `get_open_orders(account_number)` | `get_equity_orders(state="new")` 호출 |
| `get_quotes(symbols)` | `get_equity_quotes` 호출 |
| `write_snapshot(dict)` | `reports/broker_snapshots.jsonl` append (계정번호 마스킹, `real_orders_placed=0` 강제) |
| `latest_snapshot()` | 최근 스냅샷 1건 (없으면 None) |
| `place_/cancel_/review_*` | **항상 `ReadOnlyModeError`** (enabled 무관) |

## 입력/출력
- 입력: 읽기 메서드는 account_number(str) 또는 symbols(iterable[str]).
- 출력: MCP 도구 원본 dict(트랜스포트가 반환). 스냅샷은 마스킹된 dict.

## 엣지 케이스
- `enabled=False` 또는 `transport=None` → 읽기 메서드는 `RobinhoodMcpNotConfigured`.
- 화이트리스트 밖 도구명 → `ReadOnlyModeError`.
- 스냅샷 파일 부재/손상 라인 → `latest_snapshot()`는 None 또는 손상 라인 skip(크래시 없음).
- 계정번호는 로그/스냅샷에서 마지막 4자리만(`mask_account`).

## 불변식 (CRITICAL)
- 기본 비활성, 무네트워크, 무인증 (명시적 enable + transport 주입 전까지).
- write/action 메서드 영구 차단 → `ReadOnlyModeError`.
- 시크릿(토큰)·전체 계정번호를 저장/로그하지 않는다.
- `real_orders_placed`는 이 경로로 증가 불가(주문 경로 부재). live_auto 미관여.

## 다음 단계 (이 spec 범위 밖)
- 대화형 OAuth 부트스트랩(브라우저) → refresh token 안전 보관(.env/keychat, .gitignore).
- `mcp` Python 패키지 + OAuth 클라이언트로 `transport` 구현(읽기 도구만).
- `GET /api/broker/snapshot` 라우터 결선(최근 스냅샷 read-only 노출).
- 주문 경로는 별도 모듈에서 ExecutionGate 뒤로만 — 이 클라이언트에는 영원히 추가 금지.
