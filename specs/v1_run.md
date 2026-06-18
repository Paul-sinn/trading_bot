# SPEC: v1_run (v1 백테스트 실행 → 매매일지·리포트 → go/no-go 판단 근거)

헌장 `docs/STRATEGY.md` §10: step6 어댑터로 무료 일봉을 받아 step5 엔진으로 v1 백테스트를 실행하고,
**매매일지 + 성과 리포트 + 게이트 체크리스트**를 산출한다. ⚠️ **go/no-go 최종 판정은 사람이 한다** —
이 모듈은 *판단 근거(숫자)*만 만든다. 자동 라이브 진입·실주문 절대 없음(연구·측정 단계).

관련 문서: `docs/STRATEGY.md` §6(목표·MDD)·§9(승리=위험조정 우위)·§10(게이트·v1), ADR-001/002,
`algorithms/backtest.py`(step5), `agents/data_adapter.py`(step6 provider 주입).

CRITICAL: 테스트는 **네트워크 금지** — `MockDailyProvider` 주입. provider는 주입형(엔진/리포트가 소스에 안 묶임).
CRITICAL: **실거래·실주문·자동 라이브 진입 금지.** v1 결과만으로 라이브 greenlight 결론 금지(생존편향, 헌장 §3).
이 모듈은 I/O(어댑터 호출)라 `agents/`에 둔다(ADR-001). 집계/게이트 판정 로직은 순수 함수로 분리(테스트).

## 데이터 정렬
- `_align`: universe 심볼 프레임 + SPY + VIX를 **공통 날짜 인덱스(교집합)**로 reindex → 엔진의 "정렬" 가정 충족.

## dataclass (frozen)
```python
GateThresholds: sharpe_min=1.0, mdd_design=0.15, mdd_hard=0.20   # 시작값(헌장 §10/§6, §12 최종화 OPEN)
GateChecklist: sharpe_pass, beats_spy_sharpe, mdd_design_pass, mdd_hard_pass, overall_pass: bool
               + 사용한 임계값·실측값(근거)
ExitLayerAB: name: str, total_return, sharpe, max_drawdown: float, total_trades: int
FractionCalibration: current_fraction, realized_mdd, mdd_target: float, suggested_fraction: float, note: str
V1Report: survivorship_warning: str(상단), strategy: BacktestResult, gate: GateChecklist,
          exit_layer_ab: list[ExitLayerAB], fraction_calibration: FractionCalibration
```

### step8 갱신 (다중 벤치마크 로드 + 노출도 — 헌장 §9)
- `run_v1`은 어댑터로 **QQQ·SMH도 로드**(SPY 외에)해 `_align`으로 공통 인덱스에 맞춰 `run_backtest(..., benchmark_data=...)`로 주입.
  → `strategy.benchmarks`에 SPY·QQQ·SMH 3개. 어댑터에 해당 심볼이 없으면 그 벤치마크만 생략(에러 없이).
- `benchmark_symbols: tuple[str,...] = ("QQQ", "SMH")` 파라미터(SPY는 spy_symbol로 항상). 청산 레이어 A/B 런들에도 동일 주입.
- `format_report`: **SPY·QQQ·SMH 나란히**(CAGR·Sharpe·MDD) + **노출도(time_in_market·avg_concurrent)** 출력.
- ⚠️ 게이트 판정 기준의 QQQ/SMH 전환은 **step10**에서(이 step은 측정·표시까지). evaluate_gate는 step8에선 불변.

## 함수

### `evaluate_gate(sharpe, max_drawdown, benchmark_sharpe, thresholds) -> GateChecklist` (순수)
- `sharpe_pass = sharpe >= thresholds.sharpe_min` (헌장 §10① 비용後 Sharpe ≥ ~1.0).
- `beats_spy_sharpe = sharpe > benchmark_sharpe` (헌장 §9 — 승리=위험조정 우위).
- `mdd_design_pass = max_drawdown <= thresholds.mdd_design` (≤15% 설계목표).
- `mdd_hard_pass = max_drawdown <= thresholds.mdd_hard` (≤20% 하드차단).
- `overall_pass = sharpe_pass AND beats_spy_sharpe AND mdd_hard_pass` (하드 게이트; 설계 MDD는 목표로 별도 보고).
- ⚠️ overall_pass=True여도 **판정은 사람 몫**(생존편향 — 통과는 "다음 단계 검토 가능" 의미일 뿐).

### `calibrate_fraction(current_fraction, realized_mdd, mdd_target) -> FractionCalibration` (순수)
- 헌장 §6·§7: `fraction`은 MDD 설계 ≤15%로 역튜닝되는 governor.
- `suggested = current × (mdd_target / realized_mdd)` (realized_mdd>0일 때, [0,current] 클램프 — 더 키우지 않음).
  realized_mdd ≤ target면 suggested=current. realized_mdd=0이면 current 유지. **적용은 사람 몫**(제안만).

### `run_v1(provider, universe, start=None, end=None, *, spy_symbol="SPY", params, costs, thresholds) -> V1Report`
- 어댑터로 로드(`get_ohlcv`/`get_vix`) → `_align` → `run_backtest`(full layers) → strategy.
- **청산 레이어 A/B**: baseline(①+④) → +②본전 → +③부분익절 → +⑤⑦(full). 각 `run_backtest` → `ExitLayerAB`.
- `evaluate_gate`·`calibrate_fraction` 호출. `survivorship_warning`을 리포트 상단에 채운다.

### `format_report(report) -> str`
- 사람이 읽는 텍스트: 상단 생존편향 경고 → 전략 vs SPY 나란히(Sharpe/Sortino/MDD/CAGR/승률/PF/expectancy) →
  게이트 체크리스트(pass/fail) → 청산 레이어 A/B 표 → fraction 캘리브레이션 → 매매일지 요약. **GO/NO-GO는 사람**임을 명시.

## 엣지케이스
- 0거래/짧은 데이터: 예외 없이 리포트 생성(지표 0, gate 대부분 fail).
- 정렬 후 공통 인덱스 부족: 거래 0(안전).

## 비범위
- 자동 라이브 진입·실주문(executor), 생존편향 없는 벤더 재검증(라이브 전, 별도), 페이퍼/소액 라이브(게이트 통과 後 사람 결정).
- 1시간봉(v2). `scripts/run_v1.py`는 실데이터 CLI(네트워크) — 수동 실행, CI 아님.

---

## step10 갱신 (게이트 = QQQ/SMH 기준 + 양방향 캘리브레이션, 헌장 §9)

- **게이트 기준 전환(SPY 단독 금지)**: `evaluate_gate(sharpe, max_drawdown, cagr, benchmarks: dict[str,Benchmark], thresholds)`.
  - `beats_benchmarks` = 전략 Sharpe > **QQQ·SMH 중 강한 쪽**(가장 빡센 경쟁자) Sharpe. (QQQ/SMH 없으면 전체 벤치마크 max로 폴백.)
  - `abs_return_ok` = 전략 CAGR ≥ (최고 벤치마크 CAGR × `abs_return_floor`) — "절대수익이 인덱스에 크게 안 뒤짐"(헌장 §9 v1 교훈).
  - `overall_pass` = sharpe_pass AND beats_benchmarks AND abs_return_ok AND mdd_hard_pass. (design MDD는 별도 보고.)
- `GateThresholds`에 `abs_return_floor: float = 0.5` 추가. `GateChecklist`: beats_spy_sharpe → **beats_benchmarks**, **abs_return_ok** 추가, toughest_benchmark_sharpe·best_benchmark_cagr·cagr 보고.
- **calibrate_fraction 양방향(헌장 §6)**: MDD > mdd_target(15%) → 축소 제안 / MDD < mdd_floor(12%) → **상향 제안**(예산 미사용) /
  band[12%,15%] 내 → 유지. realized_mdd ≤ 0 → 유지(분모 안전). `calibrate_fraction(current, realized_mdd, mdd_target=0.15, *, mdd_floor=0.12)`.
- run_v1: evaluate_gate에 strategy.cagr·benchmarks 전달. format_report 게이트 섹션을 QQQ/SMH·절대수익·노출도 반영.
- ⚠️ go/no-go는 사람: "비용·편향 감안해도 QQQ/SMH를 위험조정으로 이기고 절대수익도 부끄럽지 않은가?" 생존편향 제거 전 라이브 금지.
