# SPEC: oos_validate (생존편향 없는 약세장 OOS 재검증 러너)

헌장 `docs/STRATEGY.md` §10②(아웃샘플·워크포워드 — 커브피팅 킬러, 가장 중요)·§3(생존편향 없는 벤더 재검증).
point-in-time 유니버스(상폐종목 포함)로 **약세장(2018/2022) 등 윈도우별 OOS 백테스트**를 돌려, 편향 제거
後에도 엣지가 남는지 본다. working fraction은 **보수적 max_risk_pct≈0.015**.

관련 문서: `docs/STRATEGY.md` §3/§9/§10, ADR-001(I/O 격리)/ADR-002, `algorithms/universe.py`(point-in-time 선정),
`agents/data_adapter.py`(PointInTimeProvider), `agents/v1_run.py`(run_v1·V1Report·게이트).

CRITICAL: I/O라 `agents/`(ADR-001). 테스트는 네트워크·유료 SDK 없이 MockPointInTimeProvider로. 실데이터(CSV/Norgate)
주입은 수동 실행. **실거래·자동 라이브 진입 금지.** 생존편향 제거·OOS 통과 전 greenlight 금지(헌장 §3·§10).

## 함수

### `run_oos_validation(provider, windows, *, max_risk_pct=0.015, spy_symbol="SPY", benchmark_symbols=("QQQ","SMH")) -> dict[str, V1Report]`
- `windows: dict[str, tuple[str, str]]` — 이름→(start, end). 예: `{"2018-bear": ("2018-01-01","2018-12-31"), "2022-bear": (...), "full-oos": (...)}`.
- 각 윈도우: `as_of = start` 시점 **point-in-time 유니버스** = `provider.get_constituents(as_of)`(상폐종목 포함, 미상장 제외).
  → `run_v1(provider, universe, start, end, spy_symbol=, benchmark_symbols=, params=BacktestParams(max_risk_pct=max_risk_pct))`.
- 유니버스 빈 윈도우는 건너뛴다(결과에서 생략). 반환: 윈도우 이름→V1Report.
- ⚠️ working fraction은 보수적(0.015) — 편향 든 v1 단계 값을 그대로 쓰지 않는다(헌장 §6 주의).

### `format_oos_report(results) -> str`
- 윈도우별 한 줄: 전략 Sharpe/CAGR/MDD vs QQQ Sharpe/CAGR, 게이트 overall, 노출도. 상단에 "생존편향 제거 검증" 표기.
- **GO/NO-GO는 사람**: "약세장 포함·편향 제거 後에도 QQQ를 위험조정으로 이기는가?"

## 엣지케이스
- provider에 벤치마크(QQQ/SMH) 없음 → 해당 벤치마크 생략(run_v1이 처리).
- 윈도우 데이터 짧음/유니버스 0 → 거래 0 리포트(예외 없음).

## 비범위
- 실 벤더 데이터 로드(CsvPointInTimeProvider/NorgateProvider가 담당), 페이퍼/라이브(게이트 통과 後 사람).
- 파라미터 자동 최적화(과최적화 금지 — working fraction 고정 보수값).
