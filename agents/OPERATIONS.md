# Operations Guide — 산업·회사 Deep Dive 시스템

## 0. 시스템 위치
형우의 Alpha 엔진 두 layer 구조 중 **Deep-dive layer** — 발굴 종목의 산업·회사 본질 deep dive + FDD risk verification 보강 부록.

## 1. 시스템 구성

| Layer | Master | 산출 | Subagent |
|---|---|---|---|
| 본문 (산업·회사 Deep Dive) | `pe-research-master` | Part 1 산업 + Part 2 회사 + Part 4 종합 | 7 |
| 부록 (FDD Risk Verification) | `public-fdd-master` | Part 3 (본문 보강 layer) | 8 |

## 2. 산출물 형식
- **한국 증권사 애널리스트 리포트 본문 스타일** (미래에셋·삼성·NH·키움 톤)
- **valuation / target price / BUY-SELL / forecast / 상승여력 / 적정가 일체 X**
- 사실 · 메커니즘 · 구조 deep dive 중심
- 회사 발화 / 컨센서스 인용은 OK (본인 forecast X)
- FDD 부록은 본문 보강 layer (메인은 본문)

## 3. 운영 모드

### Mode A — 본문 + 부록 (default)
1. `pe-research-master` 호출 → Part 1·2·4 본문 산출
2. `public-fdd-master` 호출 → Part 3 부록 산출
3. 두 산출물 결합

### Mode B — 본문만
`pe-research-master` 만 호출.

### Mode C — 부록만 (이미 본문 보유 시)
`public-fdd-master` 만 호출.

## 4. 호출 예시

```
"삼성전자 산업·회사 deep dive."
→ Mode A → 본문 + 부록 두 산출물.

"한화에어로스페이스 산업 분석만 깊게."
→ Mode B → 본문만.

"이미 NAVER 본문 있음. FDD 부록 추가."
→ Mode C → 부록만.
```

## 5. 모델 권장
- Master 2 (`pe-research-master`, `public-fdd-master`): Opus
- Specialists 15 + Fact-checker / Gatekeeper / FDD-fact-checker: Sonnet

## 6. Anti-Pattern (절대 금지)
- ❌ 추정·예측 어휘: target price / BUY-SELL / 상승여력 / 적정가 / "전망" / "예상" / "추정" / Forward PL / DCF
- ❌ "이 회사가 매력적·저평가" 류 평가
- ❌ Subagent 호출 사실 노출
- ❌ 본문 deep dive 표면적 종결 (메커니즘 누락)
- ❌ FDD 부록에 internal-data 가정·추정 (Normalized EBITDA 단정 등)
- ❌ FDD 부록이 본문 (Part 1·2) 영역 침범 (산업·회사 사업 분석은 Research Master 영역)

## 7. 폐기 파일
다음 두 파일은 frame 잘못 잡았던 시도의 산물. 무시 또는 삭제 가능:
- `HANDOFF.md` (jsonl schema)
- `watchlist.html` (HTML dashboard)

## 8. 한계·면책
- 공시 disclosure quality 한계는 본문 / 부록에 명시.
- 본 시스템은 의사결정 대체 X — 산업·회사·회계 layer 의 fact base · 메커니즘 정리.
- 주가 예측 / valuation / 매수 매도 의견은 사용자 본인 영역.
