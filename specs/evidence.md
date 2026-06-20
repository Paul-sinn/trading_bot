# SPEC: evidence (CandidateContext 자동 구성)

스캐너/데이터 출력에서 후보별 `CandidateContext`(증거 + 사이징)를 **자동 구성**한다. 수동으로 증거
불리언을 채우지 않고, 후보의 signal/filter + OHLCV + 시장 데이터에서 파생한다. 데이터 조회/조립 I/O라
agents/에 둔다.

관련: `agents/scanner.py`(Candidate: signal/filter/detail), `algorithms/signals.py`(Signal,
relative_strength), `algorithms/filters.py`(FilterResult.volume, _atr), `algorithms/regime.py`
(classify_regime), `algorithms/sizing.py`(position_size, stop_loss_price, stop_loss_pct,
per_trade_risk_pct), `agents/phase1_flow.py`(CandidateContext).

CRITICAL: 실브로커/Robinhood/라이브 없음. real orders_placed=0. 슬리피지/체결 모델 없음.
전략 시그널 로직/튜닝 변경 없음 — scanner/algorithms 함수를 재사용만 한다.

CRITICAL (fail-closed): 증거를 만들 데이터가 없으면 해당 증거를 **False/무효**로 둔다(이후 hard-veto가
veto). 데이터 품질 불량이면 사이징을 무효(qty 0, stop 0, per_trade inf)로 만들어 candidate를 막는다.
regime 산출 실패 → None(hard-veto가 막음).

## 증거 매핑 (사용자 요구 8종)
| 증거 | 출처(재사용) |
|---|---|
| market regime | `classify_regime(spy_prices, vix)` |
| trend confirmation | `candidate.signal.overall == BULLISH` |
| volume confirmation | `candidate.detail["filter"].volume` |
| relative strength | `relative_strength(close, benchmark, lookback)`(benchmark 없으면 signal.rs, 둘 다 없으면 False) |
| liquidity/spread | ADV = mean(close×volume, lookback) ≥ min_dollar_volume |
| data quality | OHLCV NaN/길이/양수/high≥low 검사 |
| event risk | `event_provider.is_clear(symbol)`(provider 없으면 False = fail-closed) |
| technical confirmation | trend AND volume AND relative_strength (알고리즘 확인 = 뉴스 단독 아님) |

사이징(파생): `stop_loss_pct`=ATR스탑/entry, `per_trade_risk_pct`=position_size risk/equity, `quantity`.
`tier_exposure_ok`=True(dry-run 플랫 포트폴리오 가정 — 포지션 없음), `ipo_data_ok`=len≥ma_period(상장이력 충분).

## 데이터 모델
- `CandidateContext`에 transparency 필드 추가: `trend_confirmed`, `volume_confirmed`,
  `relative_strength_confirmed`(기본 False). `technical_confirmation`은 이 셋의 AND(빌더가 설정).
  `regime`은 `Regime | None`(산출 실패 시 None → fail-closed).
- `EventRiskProvider` Protocol: `is_clear(symbol) -> bool`. `MockEventRiskProvider`(default=False=fail-closed).
- `EvidenceParams`(frozen): account_equity, max_risk_pct=0.02, kelly_f=0.25, appetite_weight=1.0,
  atr_period=14, atr_multiplier=2.0, min_dollar_volume=1e7, rs_lookback=63, ma_period=200, adv_lookback=20.

## 함수
### `build_candidate_context(candidate, df, *, spy_prices, vix, params, benchmark_prices=None, event_provider=None) -> CandidateContext`
증거 8종 + 사이징을 파생해 CandidateContext를 만든다. fail-closed 규칙 적용.

### `async build_contexts(candidates, price_provider, *, spy_prices, vix, params, benchmark_prices=None, event_provider=None) -> dict[str, CandidateContext]`
각 후보의 df를 동일 price_provider로 가져와 build_candidate_context로 컨텍스트 dict 생성(phase1_flow 입력).
scanner를 바꾸지 않고 같은 provider 재사용.

## 엣지케이스 / 비범위
- benchmark 없음 → rs False → technical False → veto.
- event_provider 없음 → event_risk False → veto.
- ADV < 임계 → liquidity False → veto. 데이터 NaN/짧음 → data_ok False + 사이징 무효 → veto.
- regime BEARISH/PANIC → hard-veto가 막음.
- 비범위: 슬리피지/체결, 포트폴리오 상태 기반 tier_exposure 실측, event 캘린더 실연동, 전략/시그널 변경.
