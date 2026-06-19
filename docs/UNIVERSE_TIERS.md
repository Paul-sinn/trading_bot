# 유니버스 티어 & 리스크 정책 (Universe Tiers & Risk Policy)

> 이 문서는 헌장 `docs/STRATEGY.md` §3(유니버스)·§6(리스크)에 종속되는 **운영 정책 명세**다.
> 헌장이 "무엇을·왜"를 정의하면, 이 문서는 "어떤 티어·티커를, 어떤 집중도/리스크 모드로"를 정의한다.
> 충돌 시 헌장이 우선. 변경은 헌장 → 이 문서 → 코드 순.
>
> 개정일: 2026-06-19 · 상태: **헌법 개정 + 티어 감사 단계** (전략/주문/브로커 코드는 아직 만들지 않음).

## 0. 성격 (변하지 않는 원칙)

- 이 시스템은 **Quant-core, AI-led** 트레이딩 시스템이다. 주도 신호는 **가격·거래량·추세·레짐·리스크**.
- 기본 전략은 **스윙 트레이딩**(빨라야 데이 트레이딩). **1분봉 스캘핑은 하지 않는다**(MCP/API 호출 지연 → 초단타 부적합).
- 뉴스/SNS/AI 판단은 **보조 evidence**이며 **RiskGate를 override할 수 없다**(헌장 §4·ADR-003/005).
- 공격성은 허용하되 **risk-gated concentration**으로만 — 무제한 몰빵이 아니라 게이트·한도 안에서의 집중.
- ⛔ **수익률 보장 표현 금지(No return guarantee).** 어떤 티어·모드도 수익을 보장하지 않는다.
- ⛔ **자동 주문/라이브 실행 코드 없음.** 이 문서는 정책이지 실행 권한이 아니다.

## 1. 유니버스 철학 — 컴퍼스 vs 매매대상

빅테크·ETF는 매매 대상이기도 하지만 **주된 역할은 market compass / regime indicator**다. 비트코인이 코인
시장 방향을 잡듯, **QQQ/SMH/XLK + 메가캡 리더**를 AI/테크/고베타 성장주의 이정표로 쓴다. 단, **맹신하지 않는다.**

| 컴퍼스 상태 | 행동 |
|---|---|
| QQQ/SMH/XLK/메가캡 리더 **강세** | high-beta tech 공격 허용 |
| **애매** | position size 축소 |
| **약세** | 신규 진입 제한 또는 금지 |
| VIX 급등 / market risk-off | **RiskGate가 신규 진입 veto 가능** |

## 2. 티어 구조 & 역할

| 티어 | 역할 | 매매 |
|---|---|---|
| **Tier 0** | Market Compass / Regime Only | ❌ 매매 안 함(지표 전용) |
| **Tier 1** | Mega-cap Compass + Tradable Leaders | △ 리더는 매매, 나머지 조건부 |
| **Tier 2** | Liquid Momentum Core | ✅ 주력 swing/momentum 후보 |
| **Tier 3** | AI Infra / Power / Data Center | ✅ 테마 |
| **Tier 4A** | Large Aerospace / Defense | ✅ 안정적 sub-tier |
| **Tier 4B** | Space / IPO / High-vol Aerospace | △ 집중 제한 |
| **Tier 5** | High-Beta AI / Quantum / Speculative | △ small only, 전용 프로파일 |
| **Tier 6** | Crypto Equity Beta | △ crypto 레짐 종속 |

> Tier 0~6 전부 **현물 주식/ETF**다(헌장 §3 "equity spot only" 불변). Tier 6는 *크립토 자체*가 아니라
> 크립토 베타가 큰 **주식**(MSTR 등)이다 — 마진·옵션·선물·코인 직매매는 여전히 전면 OFF.

## 3. Concentration Mode (계좌 규모별)

초기 계좌 구간에서는 분산보다 **집중 매매**를 허용한다(헌장 §3의 "섹터 분산" 원칙을 **risk-gated concentration**으로 개정).
단 **capital deployed(투입)와 capital at risk(위험)를 분리**한다.

```
account_loss_pct = position_weight × stop_loss_pct
  예) 90% 포지션 × 7% 스탑 = 계좌손실 6.3%
      100% 포지션 × 10% 스탑 = 계좌손실 10%
```

| Phase | 계좌 | 포지션 정책 |
|---|---|---|
| **1** | $1,000~3,000 | 집중 모드. Tier 0~2: 80~100% / Tier 3: 70~90% / Tier 4A: 60~80% / Tier 4B: 50~70% / **Tier 5: 집중 금지, small only** / Tier 6: 보수적(Tier 2/5 성격에 맞춤) |
| **2** | $3,000~5,000 | 1~2 포지션. main 60~80% / secondary 20~40% |
| **3** | $5,000~10,000 | 2~3 포지션. main 40~60% / secondary 20~30% |
| **4** | $10,000+ | 포트폴리오 모드. 3~5 포지션. Tier 5 speculative sleeve 별도 제한 |

> ⚠️ Concentration은 **헌장 §6의 MDD 하드차단 20%를 무효화하지 않는다.** 포지션 집중은 허용하되, 포트폴리오
> 레벨 MDD 20% 터치 = 디레버리지/정지(서킷브레이커)는 그대로 유효. daily/weekly/consecutive loss 한도(§8)도 상위 제약.

## 4. Risk Mode B / C

**기본 = B mode.**

**B Mode (기본 aggressive swing)**
- 한 거래의 **계좌 손실 한도 최대 −7%**
- Tier 0~4 적용 가능 · Tier 4B는 50~70% position cap 유지 · Tier 5는 집중 금지

**C Mode (예외적 aggressive)**
- 한 거래의 **계좌 손실 한도 최대 −10%**
- **Tier 0~2까지만** 허용 (Tier 2는 whitelist만). Tier 3/4A/4B/5 **금지**
- **재진입 거래 금지 · 최근 연속손실 상태 금지 · market regime이 risk-on 또는 strong neutral일 때만**
- stop loss·liquidity·spread·relative strength·trend·volume expansion **전부 통과 필수**

**Tier 2 C-mode whitelist**: PLTR · COIN · HOOD · VRT · ~~SMCI~~ · CRWD · ARM · MU
> ⚠️ 감사 결과 **SMCI는 whitelist에서 제외 권고**(§7 감사: 2024 회계·상폐 리스크). needs_review.

**Tier 2 B-mode only**: NET · DDOG · HIMS · SNOW · MDB · TSM · ASML

## 5. Tier 5 Special Profile

Tier 5는 일반 알고리즘을 그대로 적용하지 않고 **전용 Risk/Entry/Exit 프로파일**을 둔다(별도 알고리즘 신설 아님).
- small position only · no concentration · no C mode · no averaging down
- entry는 **더 강한 confirmation 요구** · ATR 기반 **wider stop** 허용 · position size 더 작게
- trailing stop은 **늦게** 시작 · 강한 무브 후 partial profit 허용 · **Tier 5 총 노출 cap 필수**
- 기본은 **watchlist 또는 small-entry experimental sleeve**.

## 6. Entry / Exit / Re-entry / RiskGate (요약 — 헌장 §1·§7-2·§8과 정합)

**Entry (High-conviction)**: trend_score · volume_score · **상대강도 vs QQQ/SMH** · regime risk-on|neutral ·
liquidity/spread · 명확한 stop · R/R ≥ 1:2 선호 · 결측/이상치 없음. **뉴스/SNS·AI 단독 매수 금지**(confidence boost나 risk warning으로만).

**Exit**: initial invalidation stop 필수 · ATR/구조 기반 스탑(과도히 타이트한 고정스탑보다) · time stop(수일 무진전 시) ·
trailing은 의미있는 수익 후 시작 · 강한 무브 partial 익절.

**Re-entry**: 손절 후 **같은 날 재진입 기본 금지**. 명확한 재돌파/재확인 시 **1회만**, **사이즈는 첫 진입보다 작게**,
**C mode 사용 불가**, 재진입 실패 시 **그날 거래 중단**. ⛔ revenge/recovery trading 금지.

**RiskGate Hard Veto** — 아래 중 하나라도 실패 시 **진입 금지(fail-closed)**:
스탑 없음 · 포지션 사이즈 계산 실패 · liquidity/spread 실패 · regime risk-off · sector/tier 노출 과다 ·
daily/weekly/consecutive loss 한도 초과 · 데이터 결측/이상치 · 데이터 부족 IPO(특례 없음) ·
earnings/FOMC/CPI/고임팩트 이벤트 리스크 미확인 · AI/news/SNS만 있고 technical confirmation 없음.

---

## 7. 티어리스트 감사 (Audit Report) — Norgate 실데이터 기반

> **이건 live greenlight가 아니라 universe audit이다.** 전략 성과 검증이 아니라 티어/티커 적격성 점검.
>
> **데이터 출처**: Norgate Data(NDU 로컬), `norgatedata` SDK, as_of **2026-06-18**. `scripts/audit_universe.py`로 산출.
> - **ADV($M)** = 최근 63거래일 Turnover(실거래 달러액) 평균.
> - **ATR%** = Wilder ATR(14) ÷ 종가 (`algorithms.filters._atr` 재사용, 전략과 동일 정의).
> - **type** = Norgate security_name/subtype. **lev** = 레버리지/인버스 여부.
> - ⚠️ **데이터 커버리지 한계**: 무료체험은 히스토리 ~2년이라 거의 모든 종목의 `listed_from`이 트라이얼
>   시작일(2024-06-19)로 잘린다 → **실제 IPO일은 데이터로 확인 불가**(SPCX만 예외, 7일). IPO/신규상장
>   판정은 도메인 지식 + 이 한계를 명시해 보수적으로. 정식 구독 후 재확인 권장.

**status 정의**: `approved`(즉시 사용 가능) · `watch`(사용하되 주의·조건부) · `needs_review`(데이터/리스크 확인 필요) · `reject`(부적합).

### Tier 0 — Market Compass / Regime Only (매매 안 함)
| ticker | ADV($M) | ATR% | type | status | 사유 |
|---|---|---|---|---|---|
| SPY | 44,523 | 1.3% | ETF | approved | 초유동 컴퍼스. 매매 안 함 |
| QQQ | 32,536 | 2.1% | ETF | approved | 나스닥100 컴퍼스(벤치마크 겸) |
| SMH | 5,235 | 4.3% | ETF | approved | 반도체 모멘텀 컴퍼스 |
| SOXX | 4,217 | 5.0% | ETF | approved | 반도체 — **SMH와 중복**, 하나로 단일화 검토 |
| XLK | 2,214 | 3.0% | ETF | approved | 테크 섹터 건강도 |
| $VIX | 0 | 12.8% | Index | approved (compass only) | 지수 — **직접 매매 불가**, 변동성 레짐 전용 |

### Tier 1 — Mega-cap Compass + Tradable Leaders (전부 초유동·적정 변동성 → approved)
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| NVDA | 33,199 | 3.6% | approved | tradable leader |
| AVGO | 10,195 | 5.1% | approved | leader (vol 약간 높음) |
| AMD | 13,805 | 6.1% | approved | leader (vol 높으나 초유동) |
| MSFT | 14,623 | 3.1% | approved | compass+조건부 |
| META | 10,645 | 3.5% | approved | compass+조건부 |
| GOOGL | 10,557 | 2.9% | approved | GOOG/GOOGL 클래스 → GOOGL로 통일 |
| AMZN | 11,169 | 3.1% | approved | compass+조건부 |
| AAPL | 13,549 | 2.4% | approved | compass+조건부 |

### Tier 2 — Liquid Momentum Core
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| PLTR | 6,253 | 5.0% | approved | 초유동 (C-WL) |
| COIN | 1,833 | 6.9% | approved | multi-tag T6 (C-WL) |
| HOOD | 2,550 | 6.2% | approved | multi-tag T6 (C-WL) |
| VRT | 2,131 | 5.7% | approved | (C-WL) |
| SMCI | 1,597 | **11.2%** | **needs_review** | ATR 11.2% 극단 + 2024 회계/상폐 이력 → **C-WL 제외 권고** |
| CRWD | 1,885 | 5.0% | approved | (C-WL) |
| ARM | 2,995 | 8.0% | watch | ADR·초유동이나 ATR 8% + 2023 IPO 이력 한정 (C-WL) |
| MU | 34,164 | 7.0% | approved | 초유동 (vol 높음) |
| NET | 951 | 6.0% | watch | vol 높음 (B-only) |
| DDOG | 1,050 | 5.6% | approved | (B-only) |
| HIMS | 676 | 6.6% | watch | 상대적 thin + 고변동 (B-only) |
| SNOW | 1,554 | 6.1% | approved | (B-only) |
| MDB | 612 | 7.0% | watch | ATR 7% 높음 (B-only) |
| TSM | 5,338 | 4.0% | approved | ADR (B-only) |
| ASML | 2,880 | 4.4% | approved | ADR (B-only) |

### Tier 3 — AI Infra / Power / Data Center
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| ETN | 1,024 | 3.7% | approved | 대형 전력기기 |
| GEV | 2,678 | 4.4% | watch | GE Vernova 2024-04 분사 — 실제 장기 이력 부족 가능 |
| CEG | 999 | 4.1% | approved | Constellation Energy |
| NRG | 378 | 4.3% | approved | |
| PWR | 773 | 4.0% | approved | Quanta Services |
| EME | 314 | 3.6% | approved | EMCOR |
| FIX | 755 | 4.9% | approved | Comfort Systems |
| DOV | 211 | 2.5% | approved | Dover (저변동·안정) |

### Tier 4A — Large Aerospace / Defense (저변동·유동 → approved)
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| LMT | 798 | 2.6% | approved | |
| RTX | 1,000 | 2.7% | approved | |
| NOC | 525 | 2.8% | approved | |
| GD | 473 | 2.3% | approved | |
| BA | 1,480 | 3.4% | watch | Boeing 운영/뉴스 리스크 (유동성 충분) |

### Tier 4B — Space / IPO / High-vol Aerospace
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| SPCX | 57,618* | **14.7%** | **needs_review** | ★ **Space Exploration Technologies(SpaceX), 2026-06-12 상장 = 데이터 7일뿐.** ADV*·ATR 신뢰 불가, IPO 직후 극단 변동성 → 커버리지 쌓일 때까지 small/보류. (사전 '비상장' 추정을 데이터가 정정) |
| RKLB | 2,694 | 9.8% | watch | Rocket Lab — 유동 OK, 고변동, 4B 집중제한 |
| ASTS | 1,926 | **13.0%** | watch | AST SpaceMobile — ATR 13% 극단, 집중제한 |
| LUNR | 454 | **16.4%** | **needs_review** | Intuitive Machines — ATR 16.4%(최고) + 상대적 thin |
| PL | 538 | 13.9% | watch | Planet Labs — 고변동 |

### Tier 5 — High-Beta AI / Quantum / Speculative (small only — 고변동은 티어 전제)
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| IONQ | 1,515 | 9.5% | watch | 양자, 유동 OK·고변동 |
| RGTI | 829 | 10.8% | watch | Rigetti — 유동 OK이나 vol 극단 |
| QBTS | 763 | 10.3% | watch | D-Wave — vol 극단 |
| SOUN | 216 | 7.3% | watch | SoundHound AI |
| BBAI | 157 | 7.9% | **needs_review** | thin + ★**레버리지 오탐**: 이름 'Big**BEAR**.ai'가 휴리스틱에 걸림 — 실제 보통주, 태그 수정 필요 |
| AI | **57** | 6.6% | **needs_review** | **유동성 낮음 $57M** + 티커 'AI' ambiguity(C3.ai) |
| PATH | 361 | 6.6% | watch | UiPath |
| SYM | **100** | 6.7% | **needs_review** | 유동성 낮음 $100M, 저float |
| SERV | **39** | 8.7% | **needs_review** | **유동성 최저 $38.8M** + 고변동 → 집중 부적합, 실험적 small only |
| APLD | 838 | 8.6% | watch | Applied Digital — 인프라/마이닝 중첩 |
| IREN | 2,341 | 8.7% | watch | multi-tag T6, 유동 OK |
| CORZ | 309 | 5.7% | watch | multi-tag T6, 2024 파산보호 졸업 |

### Tier 6 — Crypto Equity Beta (crypto 레짐 종속 → 기본 watch)
| ticker | ADV($M) | ATR% | status | 사유 |
|---|---|---|---|---|
| MSTR | 2,759 | 9.2% | watch | 'Strategy' — 초고베타 BTC 프록시, 크립토 레짐 종속 |
| MARA | 508 | 7.4% | watch | 채굴, 레짐 종속 |
| RIOT | 360 | 6.4% | watch | 채굴 |
| CLSK | 293 | 7.5% | watch | CleanSpark — 상대적 작음 |
| COIN | 1,833 | 6.9% | approved | multi-tag(Tier 2 primary) |
| HOOD | 2,550 | 6.2% | approved | multi-tag(Tier 2 primary) |
| IREN | 2,341 | 8.7% | watch | multi-tag(Tier 5) |
| CORZ | 309 | 5.7% | watch | multi-tag(Tier 5) |

### 감사 요약 (실데이터 확정)
- **status 집계**(63 unique 티커 / 67 티어 슬롯, multi-tag 4): reject **0** · needs_review **7**(SMCI·SPCX·LUNR·BBAI·AI·SYM·SERV) · watch **21** · approved **35**. 전부 Norgate에 **존재·활성**(상폐 0).
- **🔵 SPCX 정정**: SpaceX가 **실제 상장(2026-06-12)** — 사전 '비상장 reject' 추정을 데이터가 뒤집음. 단 **데이터 7일** → needs_review(IPO 직후·커버리지 부족). *데이터 기반 감사의 가치를 보여준 사례.*
- **🔴 레버리지 휴리스틱 결함**: `BBAI`('BigBear.ai')가 'BEAR' 토큰에 오탐. 실제 레버리지/인버스 ETF는 유니버스에 **0개**. → 휴리스틱을 Norgate `classification`/ETF type 기반으로 정밀화 권고(후속, 이번 단계 코드변경 X).
- **🟡 유동성 꼬리(집중매매 spread 리스크)**: `SERV`($39M)·`AI`($57M)·`SYM`($100M)·`BBAI`($157M) → Tier 5 small-only/needs_review.
- **🟡 ATR 밴드 불일치**: Tier 1~2 다수가 기존 1.5~5% 밴드 초과(AMD 6.1%·MU 7.0%·COIN 6.9%·SMCI 11.2% 등). 이 고베타 유니버스엔 **티어별 ATR 밴드**가 필요 — *관찰 결과만 기록, 공격성/밴드 값 튜닝은 이번 단계 금지(편향 없는 검증 단계로 이연).*
- **multi-tag**: COIN·HOOD(T2↔T6), IREN·CORZ(T5↔T6) — exposure **합산** 적용. SOXX↔SMH(반도체 컴퍼스 중복) 단일화 검토.
- **권고 처리**: SMCI를 C-mode whitelist에서 제외. SPCX는 커버리지 ≥ 충분 전 보류. needs_review 7종은 정식 구독 후 spread/IPO 재확인.

> ⚠️ 이건 universe audit이지 live greenlight가 아니다. 매매·라이브는 헌장 §10 게이트(생존편향 없는 약세장 검증 등)와 별개로 진행되며, 자동 라이브 주문 코드는 만들지 않는다.
