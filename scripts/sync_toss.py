"""
update_portfolio.py
===================
토스증권 Open API로 보유종목 현재가를 조회해 wealth_dashboard.json을 업데이트.

- PENSION_*, 부동산, 보증금, 비트코인, cashflow/bridge 섹션은 건드리지 않음
- 토스증권에 있는 주식/ETF 종목만 value / pnl / ret 갱신
- kpi, subKpi, riskRatios, assetGroup, invGroup, top5Concentration, riskMonitor 재계산

환경변수 (GitHub Secrets → Actions env):
  TOSS_APP_KEY    : 토스증권 Open API 앱 키
  TOSS_APP_SECRET : 토스증권 Open API 앱 시크릿
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
REPO_ROOT     = Path(__file__).parent.parent
WEALTH_JSON   = REPO_ROOT / "data" / "wealth_dashboard.json"
KST           = timezone(timedelta(hours=9))

APP_KEY    = os.environ["TOSS_APP_KEY"]
APP_SECRET = os.environ["TOSS_APP_SECRET"]

# 수동 관리 ticker 접두사 — 이 항목은 API로 업데이트 안 함
MANUAL_PREFIXES = ("PENSION_",)
MANUAL_ASSET_CLASSES = ("퇴직연금",)

# ──────────────────────────────────────────────
# 토스증권 Open API
# ──────────────────────────────────────────────
BASE_URL = "https://openapi.tosssecurities.com"  # 실제 엔드포인트 확인 필요

def get_access_token() -> str:
    """OAuth2 접근 토큰 발급"""
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_kr_balance(token: str) -> list[dict]:
    """국내 주식 잔고 조회 → [{ticker, name, value_krw, cost_krw, qty}, ...]"""
    resp = requests.get(
        f"{BASE_URL}/v1/domestic/balance",
        headers={
            "Authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()

    positions = []
    for item in raw.get("output1", []):  # 필드명은 실제 응답 확인 후 수정
        positions.append({
            "ticker":    item.get("pdno") or item.get("ticker"),    # 종목코드
            "name":      item.get("prdt_name") or item.get("name"),
            "value_krw": int(item.get("evlu_amt", 0)),              # 평가금액(원)
            "cost_krw":  int(item.get("pchs_amt", 0)),              # 매입금액(원)
            "qty":       int(item.get("hldg_qty", 0)),
        })
    return positions


def get_us_balance(token: str) -> list[dict]:
    """해외 주식 잔고 조회 → [{ticker, name, value_krw, cost_krw, qty}, ...]"""
    resp = requests.get(
        f"{BASE_URL}/v1/overseas/balance",
        headers={
            "Authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()

    positions = []
    for item in raw.get("output1", []):
        positions.append({
            "ticker":    item.get("ovrs_pdno") or item.get("ticker"),
            "name":      item.get("ovrs_item_name") or item.get("name"),
            "value_krw": int(float(item.get("ovrs_stck_evlu_amt", 0))),  # KRW 환산 평가금액
            "cost_krw":  int(float(item.get("pchs_amt", 0))),
            "qty":       int(float(item.get("ovrs_cblc_qty", 0))),
        })
    return positions


def get_krw_cash(token: str) -> int:
    """원화 예수금 조회"""
    resp = requests.get(
        f"{BASE_URL}/v1/account/balance",
        headers={
            "Authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    return int(float(raw.get("dnca_tot_amt", raw.get("krw_cash", 0))))


# ──────────────────────────────────────────────
# 포지션 매핑 빌드
# ──────────────────────────────────────────────
def build_position_map(kr: list, us: list, krw_cash: int) -> dict[str, dict]:
    """ticker → {value_krw, cost_krw} 딕셔너리"""
    pm = {}
    for p in kr + us:
        ticker = (p["ticker"] or "").strip().upper()
        if not ticker:
            continue
        pm[ticker] = {
            "value_krw": p["value_krw"],
            "cost_krw":  p["cost_krw"],
        }
    pm["TOSS_KRW_CASH"] = {"value_krw": krw_cash, "cost_krw": krw_cash}
    return pm


def is_manual(holding: dict) -> bool:
    """수동 관리 항목 여부"""
    ticker = holding.get("ticker", "")
    asset  = holding.get("assetClass", "")
    if any(ticker.startswith(p) for p in MANUAL_PREFIXES):
        return True
    if asset in MANUAL_ASSET_CLASSES:
        return True
    return False


# ──────────────────────────────────────────────
# JSON 업데이트
# ──────────────────────────────────────────────
def update_holdings(data: dict, pm: dict[str, dict]) -> tuple[dict, int, int]:
    """consolidated/pnl 섹션 업데이트. (updated_count, skipped_count) 반환"""
    updated = skipped = 0

    for section in ("consolidated", "pnl"):
        for h in data.get(section, []):
            if is_manual(h):
                skipped += 1
                continue
            ticker = (h.get("ticker") or "").strip().upper()
            if ticker in pm:
                new_val  = pm[ticker]["value_krw"]
                new_cost = pm[ticker]["cost_krw"] if pm[ticker]["cost_krw"] > 0 else h.get("cost", h.get("cost_krw", 0))
                h["value"] = new_val
                h["pnl"]   = new_val - new_cost
                h["ret"]   = round(h["pnl"] / new_cost, 4) if new_cost > 0 else 0.0
                updated += 1
            else:
                print(f"  [SKIP] {ticker} — 토스 API 응답에 없음 (보유 없거나 ticker 불일치)")
                skipped += 1

    return data, updated, skipped


def recalculate_aggregates(data: dict) -> dict:
    """kpi, subKpi, riskRatios, assetGroup, invGroup, top5 재계산"""
    holdings = data.get("consolidated", [])

    # ── 수동 섹션 값 보존 ──
    realestate   = next((h["value"] for h in holdings if h.get("ticker") == "REALESTATE"), None)
    deposit      = next((h["value"] for h in holdings if h.get("ticker") == "DEPOSIT"), None)
    bitcoin      = data["subKpi"].get("비트코인", 0)
    # 부동산/보증금은 assetGroup 에서 읽음
    re_val   = next((g["value"] for g in data.get("assetGroup",[]) if g["name"] == "부동산"), 0)
    dep_val  = next((g["value"] for g in data.get("assetGroup",[]) if g["name"] == "보증금"), 0)
    debt     = data["kpi"]["총부채"]

    # ── 주식/ETF 합산 ──
    stock_total = sum(
        h["value"] for h in holdings
        if not is_manual(h) and h.get("assetClass") in ("주식/ETF", "현금성")
    )
    pension_total = sum(
        h["value"] for h in holdings if h.get("assetClass") == "퇴직연금"
    )
    krw_cash = next(
        (h["value"] for h in holdings if h.get("ticker") == "TOSS_KRW_CASH"), 0
    )

    total_asset = re_val + dep_val + bitcoin + stock_total + pension_total + krw_cash
    net_worth   = total_asset - debt
    invest_asset = stock_total + pension_total  # 투자자산

    # ── kpi ──
    data["kpi"].update({
        "총자산":    total_asset,
        "순자산":    net_worth,
        "투자자산":  invest_asset,
        "부채비율":  round(debt / total_asset, 8) if total_asset else 0,
    })

    # ── subKpi ──
    data["subKpi"].update({
        "현금성 자산":    krw_cash,
        "주식/ETF (통합)": stock_total,
        "퇴직연금/예금":  pension_total,
    })

    # ── riskRatios ──
    lev_etf = sum(
        h["value"] for h in holdings
        if h.get("leverage") == "Y" and not is_manual(h)
    )
    data["riskRatios"].update({
        "부채/순자산":      round(debt / net_worth, 8)   if net_worth else 0,
        "위험자산/순자산":  round(lev_etf / net_worth, 8) if net_worth else 0,
        "레버리지/순자산":  round(lev_etf / net_worth, 8) if net_worth else 0,
        "비트코인/순자산":  round(bitcoin / net_worth, 8) if net_worth else 0,
    })

    # ── assetGroup pct 재계산 ──
    for g in data.get("assetGroup", []):
        g["pct"] = round(g["value"] / total_asset, 10) if total_asset else 0
    # 주식/ETF 그룹 value 갱신
    for g in data.get("assetGroup", []):
        if g["name"] == "주식/ETF":
            g["value"] = stock_total
            g["pct"]   = round(stock_total / total_asset, 10) if total_asset else 0

    # ── invGroup pct 재계산 ──
    for g in data.get("invGroup", []):
        g["pct"] = round(g["value"] / invest_asset, 10) if invest_asset else 0

    # ── top5Concentration 재계산 ──
    sorted_h = sorted(
        [h for h in holdings if not is_manual(h) or h.get("assetClass") in ("주식/ETF",)],
        key=lambda x: x["value"], reverse=True
    )
    top5 = sorted_h[:5]
    for t in data.get("top5Concentration", []):
        ticker = t.get("ticker")
        match = next((h for h in holdings if h.get("ticker") == ticker), None)
        if match:
            t["value"] = match["value"]
            t["pct"]   = round(match["value"] / net_worth, 10) if net_worth else 0

    # ── riskMonitor 재계산 ──
    risk_map = {
        "마이너스통장 / 순자산":         round(debt / net_worth, 10)    if net_worth else 0,
        "마이너스통장 / 총자산":          round(debt / total_asset, 10)  if total_asset else 0,
        "현금성 자산 / 월 생활비(₩3M)":  round(krw_cash / 3_000_000, 6),
        "현금성 자산 / 투자자산":         round(krw_cash / invest_asset, 10) if invest_asset else 0,
        "비트코인 / 순자산":              round(bitcoin / net_worth, 10)  if net_worth else 0,
        "Top 1 단일 종목 / 순자산":      round(sorted_h[0]["value"] / net_worth, 10) if sorted_h and net_worth else 0,
        "레버리지 ETF / 순자산":          round(lev_etf / net_worth, 10) if net_worth else 0,
        "고위험자산 / 순자산":            round((lev_etf + bitcoin) / net_worth, 10) if net_worth else 0,
        "주식+ETF+BTC / 순자산":         round((stock_total + bitcoin) / net_worth, 10) if net_worth else 0,
    }
    for r in data.get("riskMonitor", []):
        item = r.get("item")
        if item in risk_map:
            r["current"] = risk_map[item]

    return data


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    print(f"[{datetime.now(KST).isoformat()}] 포트폴리오 업데이트 시작")

    # 1. 현재 JSON 로드
    with open(WEALTH_JSON, encoding="utf-8") as f:
        data = json.load(f)

    # 2. Toss API 호출
    print("  토스증권 API 토큰 발급...")
    token = get_access_token()

    print("  국내 잔고 조회...")
    kr_positions = get_kr_balance(token)
    print(f"    → {len(kr_positions)}개 종목")

    print("  해외 잔고 조회...")
    us_positions = get_us_balance(token)
    print(f"    → {len(us_positions)}개 종목")

    print("  원화 예수금 조회...")
    krw_cash = get_krw_cash(token)
    print(f"    → ₩{krw_cash:,}")

    # 3. 포지션 맵 빌드
    pm = build_position_map(kr_positions, us_positions, krw_cash)

    # 4. Holdings 업데이트
    print("  Holdings 업데이트...")
    data, updated, skipped = update_holdings(data, pm)
    print(f"    → 업데이트 {updated}건 / 수동 유지 {skipped}건")

    # 5. 집계 재계산
    print("  집계 재계산...")
    data = recalculate_aggregates(data)

    # 6. asof 타임스탬프 갱신
    data["asof"] = datetime.now(KST).isoformat()
    data["_meta"]["generated_at"] = datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S")
    data["_meta"]["builder"] = "scripts/update_portfolio.py (GitHub Actions)"

    # 7. JSON 저장
    with open(WEALTH_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now(KST).isoformat()}] 완료 — {WEALTH_JSON}")
    print(f"  순자산: ₩{data['kpi']['순자산']:,.0f}")
    print(f"  주식/ETF: ₩{data['subKpi']['주식/ETF (통합)']:,.0f}")


if __name__ == "__main__":
    main()
