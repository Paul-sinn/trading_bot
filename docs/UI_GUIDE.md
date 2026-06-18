# UI 디자인 가이드

## 디자인 원칙
1. **도구처럼 보여야 한다.** 마케팅 페이지가 아니라 매일 여는 트레이딩 대시보드. 데이터 밀도 우선.
2. **손익이 한눈에.** 상승/하락은 색으로 즉시 구분되고, 숫자는 정렬·고정폭으로 빠르게 스캔 가능해야 한다.
3. **위험은 명확하게.** 리스크% 게이지, kill-switch 상태, 봇 ON/OFF는 화면에서 가장 또렷하게 보인다.

## AI 슬롭 안티패턴 — 하지 마라
| 금지 사항 | 이유 |
|-----------|------|
| backdrop-filter: blur() | glass morphism은 AI 템플릿의 가장 흔한 징후 |
| gradient-text (배경 그라데이션 텍스트) | AI가 만든 SaaS 랜딩의 1번 특징 |
| "Powered by AI" 배지 | 기능이 아니라 장식. 사용자에게 가치 없음 |
| box-shadow 글로우 애니메이션 | 네온 글로우 = AI 슬롭 |
| 보라/인디고 브랜드 색상 | "AI = 보라색" 클리셰 |
| 모든 카드에 동일한 rounded-2xl | 균일한 둥근 모서리는 템플릿 느낌 |
| 배경 gradient orb (blur-3xl 원형) | 모든 AI 랜딩 페이지에 있는 장식 |

## 색상
### 배경
| 용도 | 값 |
|------|------|
| 페이지 | #0a0a0a |
| 카드 | #141414 |
| 입력/보조 표면 | #1a1a1a |

### 텍스트
| 용도 | 값 |
|------|------|
| 주 텍스트(숫자/제목) | text-white |
| 본문 | text-neutral-300 |
| 보조(라벨) | text-neutral-400 |
| 비활성 | text-neutral-500 |

### 데이터/시맨틱 색상
| 용도 | 값 |
|------|------|
| 상승/수익/매수 | #22c55e |
| 하락/손실/매도 | #ef4444 |
| 경고/리스크 임박 | #f59e0b |
| 중립/기본 | #525252 |

> 손익·방향성(강세/약세) 외에는 컬러를 쓰지 않는다. 차트 라인도 동일 팔레트를 따른다.

## 컴포넌트
### 카드
```
rounded-lg bg-[#141414] border border-neutral-800 p-6
```

### 버튼
```
Primary:  rounded-lg bg-white text-black hover:bg-neutral-200
Danger:   rounded-lg bg-[#ef4444] text-white hover:bg-red-600   (매도/정지)
Buy:      rounded-lg bg-[#22c55e] text-black hover:bg-green-400  (매수)
Text:     text-neutral-500 hover:text-neutral-300
```

### 입력 필드 / 슬라이더
```
입력:    rounded-lg bg-[#1a1a1a] border border-neutral-800 px-4 py-3
슬라이더: 트랙 bg-neutral-800, 핸들 bg-white. 공격적↔보수적 양 끝 라벨 명시
```

### 데이터 테이블 (거래기록)
```
헤더 text-neutral-500 text-xs uppercase, 행 구분선 border-neutral-800
숫자 컬럼은 tabular-nums text-right. 손익 컬럼만 시맨틱 색상
```

### 게이지 / 진행 바 (리스크%, 목표 진행)
```
트랙 bg-neutral-800, 채움은 값에 따라 녹색→주황→적색. 퍼센트 숫자 병기
```

## 레이아웃
- 전체 너비: 대시보드 max-w-7xl (데이터 밀도), 설정 페이지 max-w-3xl.
- 정렬: 좌측 정렬 기본. 숫자는 우측 정렬. 중앙 정렬 금지.
- 간격: 카드 내부 gap-3~4, 섹션 간 space-y-6. 사이드 내비 + 메인 콘텐츠 2열.

## 타이포그래피
| 용도 | 스타일 |
|------|--------|
| 페이지 제목 | text-2xl font-semibold text-white |
| 카드 제목/라벨 | text-sm font-medium text-neutral-400 |
| 큰 수치(총자산/손익) | text-3xl font-semibold tabular-nums |
| 본문 | text-sm text-neutral-300 leading-relaxed |
| 테이블 숫자 | text-sm tabular-nums |

## 애니메이션
- 허용: 가격/숫자 갱신 시 짧은 색 플래시(0.3s), 카드 진입 fade-in(0.3s).
- 그 외 모든 장식 애니메이션 금지. 글로우·펄스·플로팅 금지.

## 아이콘
- SVG 인라인, strokeWidth 1.5 (lucide-react).
- 아이콘을 둥근 배경 박스로 감싸지 않는다. 텍스트/숫자 옆 보조로만 사용.

## 페이지별 핵심 UI
| 페이지 | 핵심 요소 |
|--------|-----------|
| ① 대시보드 | 포트폴리오 요약 카드(총자산/오늘 손익/승률), 실시간 리스크% 게이지, 봇 ON/OFF 토글, 실시간 가격 티커 |
| ② 일간 거래기록 | 오늘 체결 테이블(티커·진입가·청산가·실현손익·AI 메모) |
| ③ 주간 거래기록 | 7일 캔들차트 + 누적 손익 라인 오버레이, 요일별 승률 히트맵 |
| ④ 방향성 & AI 분석 | 매일 9시 LLM(OpenAI) 시황 요약, 7일 방향성(강세/중립/약세) + 근거 카드 |
| ⑤ 목표 & 리스크 | 목표금액 진행 바, 드로우다운 한도·최대 포지션 크기 설정 |
| ⑥ 투자성향 설정 | 공격적↔보수적 슬라이더, 섹터 화이트/블랙리스트, 매매 시간대, 알림(슬랙/SMS) |
