# SPEC: realistic_entry_baseline (현실적 진입 베이스라인 잠금 — 문서/회귀 잠금 전용)

실험 결과를 근거로 **현실적 진입 실행 베이스라인을 next-bar-limit 3%로 고정**한다. 향후 실험이
실수로 next-open을 기본으로 승격하거나 안정적인 60일 셋업을 바꾸지 못하도록 회귀 테스트로 못 박는다.

이 스펙은 새 매매 경로/실행 코드를 추가하지 않는다. 기존 설정 표면(run_sim 기본값, 실험 러너의
VariantConfig/`_config_to_args`, NORMAL_PROFILE, exit_sensitivity 기본 콤보)이 가리키는 값을 문서화하고
잠그는 **문서 + 회귀 잠금**일 뿐이다.

CRITICAL: 실브로커/Robinhood/MCP/라이브 주문 없음. real_orders_placed는 항상 0. 스캐너/디시전/사이징/
RiskGate 변경 없음. 라이브 전략 시그널 튜닝 없음. LLM/뉴스/라이브 이벤트 API 미연결.

## 진입 실행 모델 등급
- `current` (same-bar): **참조/레퍼런스 전용 — 현실적이지 않다.** 같은 바에서 시그널·체결이 일어나
  룩어헤드 편향이 있다. 성능 상한선 측정용일 뿐 실행 후보가 아니다.
- `next-bar-limit` (buffer 3%): **주 현실적 베이스라인 후보.** 시그널 다음 바에서 한도주문 체결을
  모사한다. 슬리피지·심볼·시간창 전반에서 가장 안정적. → **기본 현실 베이스라인으로 잠금.**
- `next-open` (next-bar open): **실험 전용.** 20-심볼 풀기간에서 3% limit을 앞섰으나 로버스트니스
  검증 실패 — 우위가 MU/ARM 등 소수 심볼에 의존했고 분기별로 일관되지 않았다(`specs/execution_robustness.md`).
  슬리피지에는 강했으나 단일 심볼 의존으로 **기본 승격 금지**.

## 잠긴 베이스라인 값
- max_holding_days = 60
- stop_loss = 0.15
- trailing_stop = 0.20
- share_mode = fractional
- entry_fill_model = next-bar-limit (현실 베이스라인), 기본 CLI는 current(참조)
- entry_limit_buffer_pct = 0.03
- winner_extension = 미적용(리포트 전용 — 매매 경로에 미연결)
- gap_guard = 미적용
- weekend_exit_symbols = 기본 빈 집합(레버리지 ETF opt-in 전용, 일반주 미적용)
- real_orders_placed = 0

## 규칙
- next-open을 기본으로 승격하지 않는다. 레버리지 주말청산 코드는 유지하되 opt-in 전용.
- 일반주는 절대 주말청산 대상이 아니다. winner extension / gap guard는 적용하지 않는다.
- 90/120일 보유는 실험 변형 전용이며 기본은 60일.

## 회귀 테스트 (tests/test_realistic_entry_baseline.py)
- 현실 베이스라인이 next-bar-limit을 쓴다(실험 러너의 limit 암(arm)이 next-bar-limit/0.03).
- entry_limit_buffer_pct == 0.03, max_holding_days == 60, stop 0.15, trailing 0.20, fractional.
- next-open이 어떤 기본값도 아니다(run_sim 기본=current, VariantConfig 기본=next-bar-limit).
- winner extension / gap guard 인자가 베이스라인 설정에 없다.
- weekend_exit_symbols 기본 빈 집합. real_orders_placed == 0.

## 비범위
- 새 실행 경로, 자본 재배분 변경, 갭 가드/winner extension 적용, 라이브 적용, 전략/시그널/베이스라인 값 변경.
