# SPEC: universe (룰 기반 point-in-time 유니버스 선정 — 생존편향 제거)

헌장 `docs/STRATEGY.md` §3: 손으로 "지금 잘나가는 종목"을 고르면 백테스트가 부풀어 거짓말한다(살아남은
승자만 봄). 선정은 **룰 기반 point-in-time** — 각 역사 시점의 자격 종목을 유동성·ATR 밴드로 선정하고
레버리지/인버스 ETF를 제외하며, **상장폐지 종목도 상폐 이전 시점엔 포함**해 생존편향을 제거한다.

관련 문서: `docs/STRATEGY.md` §3(룰 기반 point-in-time·생존편향)·§10(검증 ②), ADR-002(순수 함수).

CRITICAL: **부수효과 없는 순수 함수.** I/O·네트워크·DB·전역상태·난수 금지. 입력(시점별 메트릭)만으로 결정.
CRITICAL: **미래참조 금지.** as_of 시점에 (1) 아직 상장 전 종목을 넣지 않는다, (2) 이미 상폐된 종목을
넣지 않는다. 단 **상폐 이전 시점엔 반드시 포함**한다(빼면 생존편향 재유입).

## SymbolMetrics (frozen)

| 필드 | 타입 | 의미 |
|------|------|------|
| `listed_from` | `str` (ISO `YYYY-MM-DD`) | 상장일(이 날짜부터 후보) |
| `delisted_at` | `str \| None` | 상폐일(없으면 현존). as_of < delisted_at 이면 그 시점엔 살아있음 |
| `avg_dollar_volume` | `float` | 평균 달러 거래량(유동성) |
| `atr_pct` | `float` | 일 ATR/가격 비율(변동성 밴드 판정) |
| `is_leveraged_or_inverse` | `bool` | 레버리지/인버스 ETF 여부(규칙상 제외 대상) |

## 함수

### `select_universe(metrics, as_of, *, min_dollar_volume, atr_pct_band=(0.015, 0.05), exclude_leveraged=True) -> list[str]`
시점 `as_of`(ISO 문자열)에 자격을 갖춘 심볼을 정렬해 반환한다(헌장 §3·§101 밴드 시작값).
포함 조건(모두 AND):
1. **상장됨**: `listed_from <= as_of`.
2. **미상폐**: `delisted_at is None` 또는 `as_of < delisted_at`.
3. **유동성**: `avg_dollar_volume >= min_dollar_volume`.
4. **변동성 밴드**: `atr_pct_band[0] <= atr_pct <= atr_pct_band[1]` (너무 낮음=추세없음 / 너무 높음=휩쏘갭).
5. **레버리지/인버스 제외**: `exclude_leveraged`이면 `is_leveraged_or_inverse`인 종목 제외.
반환: 조건 통과 심볼의 정렬 리스트(결정론).

날짜는 ISO `YYYY-MM-DD` 문자열로 사전식 비교(달력 순서와 일치)한다.

## 엣지케이스
- 메트릭 비어있음 → 빈 리스트.
- 경계: `listed_from == as_of` → 포함(>= ). `delisted_at == as_of` → **제외**(상폐일 당일은 거래 불가로 본다, `<`).
- ATR 정확히 밴드 경계 → 포함(폐구간).

## 비범위
- 데이터 조회(I/O) — `agents/data_adapter.py`의 PointInTimeProvider(step11 I/O).
- 큐레이션(라이브 부분집합), 섹터 분산 제약(후속). 백테스트 실행 — `agents/oos_validate.py`.
