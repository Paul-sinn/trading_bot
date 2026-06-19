# Overnight: 헌법 개정 + 유니버스 티어 감사 (통합 리포트)

> 작성: 2026-06-19 · 작성 도구: `scripts/audit_universe.py`(Norgate 실데이터, as_of 2026-06-18)
>
> ⚠️ **이 문서는 universe audit이지 live greenlight가 아니다.** 매매·라이브 진입을 승인하지 않는다.
> 이번 작업에서 **전략 로직·주문 코드·브로커 연동은 만들거나 바꾸지 않았다**(헌법/문서 개정 + 티어 감사만).
> 권위 문서는 `docs/STRATEGY.md`(헌장)·`docs/ADR.md`(ADR-012)·`docs/UNIVERSE_TIERS.md`(운영 정책). 충돌 시 헌장 우선.

---

## Part A — 헌법(Constitution) 개정 요약

### A.1 해소한 충돌 (기존 헌장 ↔ 새 정책)
| 기존 헌장 | 새 정책 | 처리 위치 |
|---|---|---|
| 섹터 **분산**·몰빵 금지·20~40종목 | AI/테크 **집중**(80~100%) | STRATEGY §3/§6 → **risk-gated concentration** |
| 섹터 ETF 예: SMH/**XLE/XLF** | XLE/XLF 제외, **XLK/SOXX** + 6티어 | STRATEGY §3, UNIVERSE_TIERS §2 |
| SPY/QQQ/VTI **제외**(벤치마크 자기모순) | SPY/QQQ/SMH = **컴퍼스/레짐 지표**(비매매) | STRATEGY §3 컴퍼스/매매 분리 |
| ATR **1.5~5% 밴드** | Tier 4B/5는 **>5% 고변동**(전용 프로파일) | STRATEGY §3 티어별 밴드 |
| 분수 켈리 working fraction ~1.5% | 거래당 계좌손실 **B −7% / C −10%** | STRATEGY §6, UNIVERSE_TIERS §4 |
| (없음) | 계좌 규모별 **Concentration Phase 1~4** | UNIVERSE_TIERS §3 |
| (없음) | **No return guarantee** 명문화 | STRATEGY §0-7 |
| 크립토 전면 OFF | Tier 6 crypto-**equity** beta(MSTR 등=주식) | 충돌 아님 — 현물주식 유지 명확화 |

### A.2 새 정책 (요지)
- **성격**: Quant-core·AI-led. 주도 신호 = 가격·거래량·추세·레짐·리스크. 기본 **스윙**(1분봉 스캘핑 금지). 뉴스/SNS/AI = 보조 evidence, **RiskGate override 불가**.
- **컴퍼스 vs 매매대상**: QQQ/SMH/XLK + 메가캡 = 레짐 이정표(강세→공격, 애매→축소, 약세→신규제한, VIX급등→veto).
- **6티어**: T0 컴퍼스전용 · T1 메가캡+리더 · T2 유동 모멘텀 코어 · T3 AI인프라/전력 · T4A 방산 · T4B 우주/고변동 · T5 스펙(small only) · T6 crypto-equity. 전부 **현물 주식/ETF**.
- **Concentration Phase**(계좌별 1~4) + **리스크 모드 B(−7%)/C(−10%)**. `account_loss = position_weight × stop_loss_pct`(투입 ≠ 위험).
- **불변 가드**: 포트폴리오 **MDD 20% 하드차단**, daily/weekly/consecutive loss 한도, **RiskGate fail-closed**, **자동 라이브 주문 코드 없음**, **No return guarantee**.
- **검증 사다리(직전 개정)**: 페이퍼 단계 제거 → ① 인샘플 → ② 약세장 OOS(라이브 전 필수) → ③ 소액 라이브.

### A.3 ADR / 테스트
- **ADR-012**(append-only) 기록. 기존 ADR 불변.
- 문서 검증 테스트 `tests/test_docs_charter.py` 6개(No-guarantee·티어·B/C·−7/−10·ADR-012·자동주문금지) 통과. 기존 테스트 회귀 없음.

---

## Part B — 유니버스 티어 감사 (Audit Report)

### B.1 방법론
- **데이터**: Norgate(NDU 로컬), `norgatedata` SDK, as_of 2026-06-18.
- **ADV($M)** = 63거래일 Turnover 평균 · **ATR%** = Wilder ATR(14)/종가 · **type/lev** = Norgate security_name·subtype.
- **커버리지 한계**: 무료체험 ~2년 → 대부분 `listed_from`이 트라이얼 시작(2024-06-19)로 잘림 = **실 IPO일 확인 불가**(SPCX만 예외). IPO 판정은 도메인 지식 + 한계 명시로 보수적. 정식 구독 후 재확인 권장.
- **status**: `approved`(즉시 사용) · `watch`(주의·조건부) · `needs_review`(데이터/리스크 확인) · `reject`(부적합).

### B.2 각 티커 status (data-backed)

**Tier 0 — Compass / Regime Only (매매 안 함)**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| SPY | 44,523 | 1.3% | approved | 초유동 컴퍼스 |
| QQQ | 32,536 | 2.1% | approved | 나스닥100 컴퍼스 |
| SMH | 5,235 | 4.3% | approved | 반도체 컴퍼스 |
| SOXX | 4,217 | 5.0% | approved | SMH와 **중복** — 단일화 검토 |
| XLK | 2,214 | 3.0% | approved | 테크 섹터 |
| $VIX | 0 | 12.8% | approved (compass only) | 지수 — **매매 불가** |

**Tier 1 — Mega-cap Compass + Tradable Leaders**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| NVDA | 33,199 | 3.6% | approved | leader |
| AVGO | 10,195 | 5.1% | approved | leader |
| AMD | 13,805 | 6.1% | approved | leader(vol 높음) |
| MSFT | 14,623 | 3.1% | approved | compass+조건부 |
| META | 10,645 | 3.5% | approved | compass+조건부 |
| GOOGL | 10,557 | 2.9% | approved | 클래스→GOOGL 통일 |
| AMZN | 11,169 | 3.1% | approved | compass+조건부 |
| AAPL | 13,549 | 2.4% | approved | compass+조건부 |

**Tier 2 — Liquid Momentum Core**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| PLTR | 6,253 | 5.0% | approved | C-WL |
| COIN | 1,833 | 6.9% | approved | multi-tag T6 · C-WL |
| HOOD | 2,550 | 6.2% | approved | multi-tag T6 · C-WL |
| VRT | 2,131 | 5.7% | approved | C-WL |
| SMCI | 1,597 | 11.2% | **needs_review** | ATR 극단 + 2024 회계/상폐 이력 → **C-WL 제외 권고** |
| CRWD | 1,885 | 5.0% | approved | C-WL |
| ARM | 2,995 | 8.0% | watch | ADR·초유동이나 ATR 8% + 2023 IPO 이력 한정 |
| MU | 34,164 | 7.0% | approved | 초유동(vol 높음) |
| NET | 951 | 6.0% | watch | vol 높음 |
| DDOG | 1,050 | 5.6% | approved | |
| HIMS | 676 | 6.6% | watch | thin + 고변동 |
| SNOW | 1,554 | 6.1% | approved | |
| MDB | 612 | 7.0% | watch | ATR 7% |
| TSM | 5,338 | 4.0% | approved | ADR |
| ASML | 2,880 | 4.4% | approved | ADR |

**Tier 3 — AI Infra / Power / Data Center**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| ETN | 1,024 | 3.7% | approved | |
| GEV | 2,678 | 4.4% | watch | 2024-04 분사 — 장기 이력 부족 가능 |
| CEG | 999 | 4.1% | approved | |
| NRG | 378 | 4.3% | approved | |
| PWR | 773 | 4.0% | approved | |
| EME | 314 | 3.6% | approved | |
| FIX | 755 | 4.9% | approved | |
| DOV | 211 | 2.5% | approved | 저변동·안정 |

**Tier 4A — Large Aerospace / Defense**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| LMT | 798 | 2.6% | approved | |
| RTX | 1,000 | 2.7% | approved | |
| NOC | 525 | 2.8% | approved | |
| GD | 473 | 2.3% | approved | |
| BA | 1,480 | 3.4% | watch | 운영/뉴스 리스크(유동 충분) |

**Tier 4B — Space / IPO / High-vol Aerospace**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| SPCX | 57,618* | 14.7% | **needs_review** | ★ SpaceX **2026-06-12 상장 = 데이터 7일.** ADV*·ATR 신뢰 불가 → 커버리지 쌓일 때까지 보류 |
| RKLB | 2,694 | 9.8% | watch | 유동 OK·고변동, 4B 집중제한 |
| ASTS | 1,926 | 13.0% | watch | ATR 극단, 집중제한 |
| LUNR | 454 | 16.4% | **needs_review** | ATR 16.4%(최고) + 상대적 thin |
| PL | 538 | 13.9% | watch | 고변동 |

**Tier 5 — High-Beta AI / Quantum / Speculative (small only)**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| IONQ | 1,515 | 9.5% | watch | 유동 OK·고변동 |
| RGTI | 829 | 10.8% | watch | vol 극단 |
| QBTS | 763 | 10.3% | watch | vol 극단 |
| SOUN | 216 | 7.3% | watch | |
| BBAI | 157 | 7.9% | **needs_review** | thin + ★**레버리지 오탐**('BigBEAR' 휴리스틱) — 태그 수정 필요 |
| AI | 57 | 6.6% | **needs_review** | **유동성 낮음 $57M** + 티커 'AI' ambiguity(C3.ai) |
| PATH | 361 | 6.6% | watch | UiPath |
| SYM | 100 | 6.7% | **needs_review** | 유동성 낮음 + 저float |
| SERV | 39 | 8.7% | **needs_review** | **유동성 최저 $38.8M** + 고변동 → 집중 부적합 |
| APLD | 838 | 8.6% | watch | 인프라/마이닝 중첩 |
| IREN | 2,341 | 8.7% | watch | multi-tag T6 |
| CORZ | 309 | 5.7% | watch | multi-tag T6, 2024 파산보호 졸업 |

**Tier 6 — Crypto Equity Beta (crypto 레짐 종속)**
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| MSTR | 2,759 | 9.2% | watch | 초고베타 BTC 프록시, 레짐 종속 |
| MARA | 508 | 7.4% | watch | 채굴 |
| RIOT | 360 | 6.4% | watch | 채굴 |
| CLSK | 293 | 7.5% | watch | 상대적 작음 |
| COIN | 1,833 | 6.9% | approved | multi-tag(T2 primary) |
| HOOD | 2,550 | 6.2% | approved | multi-tag(T2 primary) |
| IREN | 2,341 | 8.7% | watch | multi-tag(T5 primary) |
| CORZ | 309 | 5.7% | watch | multi-tag(T5 primary) |

### B.3 status 집계 (63 unique 티커 / 67 티어 슬롯, multi-tag 4)
- **approved 35** · **watch 21** · **needs_review 7** · **reject 0**. 전부 Norgate **존재·활성**(상폐 0).
- **needs_review(7)**: SMCI · SPCX · LUNR · BBAI · AI · SYM · SERV.

### B.4 핵심 발견
1. **🔵 SPCX 정정** — SpaceX(Space Exploration Technologies)가 **실제 상장(2026-06-12)**. 사전 '비상장 reject' 추정을 데이터가 뒤집음. 단 **데이터 7일**이라 needs_review. → *데이터 기반 감사의 가치.*
2. **🔴 레버리지 휴리스틱 결함** — `BBAI`('BigBear.ai')가 'BEAR' 토큰에 오탐. 실제 레버리지/인버스 ETF는 유니버스에 **0개**. → `classification`/ETF type 기반 정밀화 권고(후속, 이번 단계 코드변경 X).
3. **🟡 유동성 꼬리** — SERV $39M · AI $57M · SYM $100M · BBAI $157M = 얇음 → 집중매매 부적합, Tier 5 small-only.
4. **🟡 ATR 밴드 불일치** — Tier 1~2 다수가 기존 1.5~5% 밴드 초과(AMD 6.1%·MU 7.0%·SMCI 11.2% 등). **티어별 ATR 밴드** 필요 — *관찰만 기록, 값 튜닝은 이번 단계 금지(편향 없는 검증 단계로 이연).*
5. **multi-tag** — COIN·HOOD(T2↔T6), IREN·CORZ(T5↔T6) → exposure **합산** 적용. SOXX↔SMH 컴퍼스 중복 → 단일화 검토.

### B.5 권고 처리
- `SMCI` → **C-mode whitelist에서 제외**.
- `SPCX` → 커버리지 충분(예: ≥ 수개월) 전 **보류**(또는 실험적 small only).
- `BBAI` → 레버리지 태그 **오탐 정정**(보통주). 휴리스틱 개선은 후속.
- needs_review 7종 → 정식 구독 후 spread/IPO/유동성 **재확인**해 approved/watch/reject 확정.

---

## 제약 준수 확인 (이번 작업에서 하지 않은 것)
- ❌ 전략 로직 변경 없음 · ❌ 자동 주문/브로커 연동 코드 없음 · ❌ 공격성/리스크 값을 데이터에 맞춰 튜닝하지 않음.
- ❌ "수익 보장(guaranteed return)" 표현 없음(오히려 No-return-guarantee 명문화).
- ✅ 헌법/문서 개정 + 티어 감사 + status 부여만 수행. **live greenlight 아님.**
