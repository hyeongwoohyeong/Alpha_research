# Alpha Engine → Deep-Dive Layer Handoff Spec

## 0. Purpose
Alpha 엔진(발굴·모니터링 layer)에서 선별한 종목을 deep-dive layer(Research Master / FDD Master)로 넘기는 표준 입력 포맷.

핵심 기준:
- Master 가 입력만으로 첫 round를 자동 시작 가능해야 함
- Delta round 시 prior dossier 와 reconcile 가능해야 함
- Alpha 엔진의 screening 신호(Auto-Profile / Outsider score 등)가 thesis 빌딩 시 working hypothesis 로 살아야 함

## 1. Input Schema (JSONL one record per ticker)

```json
{
  "schema_version": "1.0",
  "ticker": "005930.KS",
  "company_name_local": "삼성전자",
  "company_name_en": "Samsung Electronics",
  "exchange": "KOSPI",
  "as_of_date": "2026-05-06",

  "screening_trigger": {
    "source": "auto_profile",
    "phase": "Phase 1",
    "score": 0.78,
    "score_components": {
      "value": 0.55,
      "quality": 0.82,
      "momentum": 0.61,
      "outsider_signal": 0.91
    },
    "trigger_reason": "Outsider Top picks 진입 (직전 분기 quality score +0.18)"
  },

  "initial_hypothesis": "AI infra capex sustain 시 메모리 가격 회복 + DDR5/HBM mix shift 수혜",
  "hypothesis_horizon_months": 18,

  "peer_set": ["000660.KS", "MU", "TSM"],

  "watchlist_metric_hints": [
    {"metric": "DRAM ASP QoQ", "direction": "up", "threshold": "+5%"},
    {"metric": "HBM revenue share", "direction": "up", "threshold": "20%"},
    {"metric": "Inventory days", "direction": "down", "threshold": "<60"}
  ],

  "priority_focus_areas": [
    "수출 인식 timing",
    "관계사 거래 비중",
    "고객 집중 (Top 5)"
  ],

  "prior_dossier_path": null,
  "prior_fdd_dossier_path": null,

  "round_trigger": "first_round"
}
```

## 2. Field Spec

| Field | Type | Required | Note |
|---|---|---|---|
| `schema_version` | string | yes | 향후 호환성 |
| `ticker` | string | yes | 거래소 suffix 포함 (e.g., `.KS`, `.KQ`) |
| `company_name_local` | string | yes | 한국 종목 시 한국 사명 |
| `company_name_en` | string | optional | 글로벌 검색용 |
| `exchange` | string | yes | KOSPI / KOSDAQ / NYSE / NASDAQ 등 |
| `as_of_date` | YYYY-MM-DD | yes | 분석 기준일 |
| `screening_trigger.source` | enum | yes | `auto_profile`, `outsider_top_picks`, `manual`, `delta_signal` |
| `screening_trigger.phase` | string | optional | Alpha 엔진 phase 명 |
| `screening_trigger.score` | float | optional | 0-1 정규화 |
| `screening_trigger.score_components` | object | optional | sub-score breakdown |
| `screening_trigger.trigger_reason` | string | yes | 사람이 read 가능한 trigger 설명 |
| `initial_hypothesis` | string | yes | Working thesis 1 paragraph |
| `hypothesis_horizon_months` | int | optional | 검증 시간 horizon |
| `peer_set` | array of ticker | optional | Master 가 peer benchmarking 시 사용 |
| `watchlist_metric_hints` | array of object | optional | Alpha 엔진이 제안하는 monitoring metric |
| `priority_focus_areas` | array of string | optional | FDD Master 우선 검증 영역 |
| `prior_dossier_path` | string \| null | optional | Research dossier 경로 (delta round 시) |
| `prior_fdd_dossier_path` | string \| null | optional | FDD dossier 경로 |
| `round_trigger` | enum | yes | `first_round`, `quarterly_update`, `catalyst_event`, `signal_change`, `time_decay` |

## 3. 사용 예시

### A. 신규 종목 첫 round (Research Master)
입력: 위 schema 그대로, `prior_dossier_path: null`, `round_trigger: "first_round"`.
출력: `tickers/[TICKER]/dossier.md` 신규 생성.

### B. 분기 update (Research Master Delta Round)
입력:
- `prior_dossier_path: "tickers/005930.KS/dossier.md"`
- `round_trigger: "quarterly_update"`
- 신규 `screening_trigger` (Alpha 엔진 신호 변동 시)
- `watchlist_metric_hints` 갱신 (이전 round 의 threshold 충족 여부 반영)
출력: 동일 파일 업데이트, Delta Log + Round History 추가.

### C. FDD trigger (Research → FDD)
Research Master 가 산출한 `dossier.md` 의 Open Questions 와 thesis pillar 를 input 으로 FDD Master 호출:
```json
{
  "ticker": "005930.KS",
  "prior_dossier_path": "tickers/005930.KS/dossier.md",
  "priority_focus_areas": ["수출 인식 timing", "관계사 거래 비중"],
  "round_trigger": "fdd_trigger"
}
```
출력: `tickers/005930.KS/fdd_dossier.md` 신규 또는 갱신.

## 4. Output Contract (Master → Alpha 엔진)

Master 호출 후 Alpha 엔진이 받을 수 있는 output:

```json
{
  "ticker": "005930.KS",
  "round_id": "R3",
  "round_date": "2026-05-06",
  "dossier_path": "tickers/005930.KS/dossier.md",
  "fdd_dossier_path": "tickers/005930.KS/fdd_dossier.md",
  "thesis_status": "strengthened|unchanged|weakened|broken",
  "top_red_flags": [
    {"finding_id": "FDD-2026Q2-007", "frame": "earnings_management", "severity": "high", "confidence": "med"}
  ],
  "watchlist_metrics": [
    {"metric": "DSO", "current": 78, "threshold": "<70", "status": "breach"}
  ],
  "next_review_trigger": {
    "type": "quarterly_update",
    "earliest_date": "2026-08-15"
  }
}
```

이 output 을 Alpha 엔진 dashboard / `watchlist.html` 에서 read.

## 5. 운영 규칙

- 1 ticker = 1 폴더 = 1 dossier + 1 fdd_dossier (누적, 갱신).
- `round_trigger` 는 항상 명시 — Alpha 엔진의 자동 schedule 과 사용자 manual 호출을 구분.
- `screening_trigger.score_components` 는 Alpha 엔진이 보유한 신호를 그대로 전달 (Master 가 thesis 빌딩 시 Why Now 섹션의 macro/industry layer 와 reconcile).
- Schema 변경 시 `schema_version` bump.
