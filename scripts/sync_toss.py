"""
sync_toss.py
============
토스증권 Open API로 보유종목 현재가를 조회해 wealth_dashboard.json을 업데이트.

■ 업데이트 대상 (토스 API 자동)
  - 토스 국내/해외 주식·ETF (account = "토스 국내" / "토스 해외")
  - TOSS_KRW_CASH 제외 (API에 잔고 endpoint 없음 — 기존값 보존)

■ 보존 대상 (수동 관리)
  - PENSION_* ticker : 퇴직연금 / 연금저축 항목
  - assetClass = "퇴직연금"

■ 환경변수 (GitHub Secrets → Actions env):
  TOSS_APP_KEY    : 토스증권 Open API 앱 키 (Client ID)
  TOSS_APP_SECRET : 토스증권 Open API 앱 시크릿 (Client Secret)

■ 공식 API 레퍼런스: https://developers.tossinvest.com/docs
  OpenAPI spec: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
"""

import os
import json
import base64
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
WEALTH_JSON = REPO_ROOT / "data" / "wealth_dashboard.json"
KST         = timezone(timedelta(hours=9))

APP_KEY    = os.environ["TOSS_APP_KEY"]     # Client ID
APP_SECRET = os.environ["TOSS_APP_SECRET"]  # Client Secret

BASE_URL = "https://openapi.tossinvest.com"

# 수동 관리 항목 — API로 업데이트 안 함
MANUAL_PREFIXES      = ("PENSION_", "TOSS_KRW_CASH")
MANUAL_ASSET_CLASSES = ("퇴직연금",)


# ──────────────────────────────────────────────
# 토스증권 Open API 호출
# ──────────────────────────────────────────────

def _auth_header() -> str:
    """Basic Auth 헤더값 생성 (APP_KEY:APP_SECRET → base64)"""
    raw = f"{APP_KEY}:{APP_SECRET}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def get_access_token() -> str:
    """OAuth2 Client Credentials 방식으로 액세스 토큰 발급"""
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        headers={
            "Authorization": _auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_account_seq(token: str) -> int:
    """계좌 목록 조회 → 첫 번째 위탁계좌의 accountSeq 반환"""
    resp = requests.get(
        f"{BASE_URL}/api/v1/accounts",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["result"]
    accounts = result.get("accounts") or result.get("brokerageAccounts", [])
    if not accounts:
        raise RuntimeError("토스증권 계좌가 없습니다.")
    # accounts[0].brokerageAccount.accountSeq 또는 accounts[0].accountSeq
    acc = accounts[0]
    if isinstance(acc, dict) and "brokerageAccount" in acc:
        return int(acc["brokerageAccount"]["accountSeq"])
    return int(acc.get("accountSeq", 1))


def get_holdings(token: str, account_seq: int) -> dict:
    """보유 주식 전체 조회 (국내 KR + 미국 US 통합)

    반환 예시 (result.items[]):
      symbol              : "476290" (KR 6자리) | "SOXL" (US 티커)
      name                : "KODEX SK하이닉스단일종목레버리지" | "SOXL"
      marketCountry       : "KR" | "US"
      currency            : "KRW" | "USD"
      quantity            : "2500" (string)
      lastPrice           : "39200" | "28.5" (string)
      averagePurchasePrice: "28943" | "21.12" (string)
      marketValue.purchaseAmount : cost in native currency (string)
      marketValue.amount         : current value in native currency (string)
      profitLoss.amount          : P&L in native currency (string)
      profitLoss.rate            : return rate e.g. "0.3543" = 35.43% (string)
    """
    resp = requests.get(
        f"{BASE_URL}/api/v1/holdings",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tossinvest-Account": str(account_seq),
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["result"]


def get_usd_to_krw(token: str) -> float:
    """현재 USD→KRW 환율 조회 (참고용 표시 환율, 1분 갱신)"""
    resp = requests.get(
        f"{BASE_URL}/api/v1/exchange-rate",
        headers={"Authorization": f"Bearer {token}"},
        params={"baseCurrency": "USD", "quoteCurrency": "KRW"},
        timeout=15,
    )
    resp.raise_for_status()
    return float(resp.json()["result"]["rate"])


# ──────────────────────────────────────────────
# 포지션 매핑 빌드
# ──────────────────────────────────────────────

def build_position_map(
    holdings_result: dict,
    usd_to_krw: float,
) -> dict[str, dict]:
    """Toss API 응답 → {lookup_key: {value_krw, cost_krw, pnl_krw, ret, shares}} 매핑

    lookup_key:
      US 종목 → symbol (ticker) 그대로 (예: "SOXL")
      KR 종목 → name (한국어 종목명) 으로 저장, 나중에 name 매칭에 사용
    """
    pm: dict[str, dict] = {}

    for item in holdings_result.get("items", []):
        symbol   = (item.get("symbol") or "").strip().upper()
        name     = (item.get("name") or "").strip()
        country  = item.get("marketCountry", "")  # KR | US
        currency = item.get("currency", "")        # KRW | USD
        qty      = float(item.get("quantity") or 0)
        ret_rate = float(item.get("profitLoss", {}).get("rate") or 0)  # 0.3543 = 35.43%

        mv  = item.get("marketValue", {})
        pnl = item.get("profitLoss", {})

        value_native = float(mv.get("amount") or 0)
        cost_native  = float(mv.get("purchaseAmount") or 0)
        pnl_native   = float(pnl.get("amount") or 0)

        if currency == "USD":
            value_krw = value_native * usd_to_krw
            cost_krw  = cost_native  * usd_to_krw
            pnl_krw   = pnl_native   * usd_to_krw
        else:
            value_krw = value_native
            cost_krw  = cost_native
            pnl_krw   = pnl_native

        entry = {
            "value_krw": value_krw,
            "cost_krw":  cost_krw,
            "pnl_krw":   pnl_krw,
            "ret":       ret_rate,
            "shares":    qty,
            "symbol":    symbol,
            "name":      name,
            "country":   country,
        }

        if country == "US":
            pm[symbol] = entry           # US: symbol == ticker ("SOXL", "QLD" …)
        else:
            pm[f"KR_NAME::{name}"] = entry   # KR: name으로 조회

    return pm


# ──────────────────────────────────────────────
# 매핑 판단 헬퍼
# ──────────────────────────────────────────────

def is_manual(holding: dict) -> bool:
    ticker = holding.get("ticker", "")
    asset  = holding.get("assetClass", "")
    return (
        any(ticker.startswith(p) for p in MANUAL_PREFIXES)
        or asset in MANUAL_ASSET_CLASSES
    )


def find_position(holding: dict, pm: dict) -> dict | None:
    """holding → pm에서 매칭 항목 찾기

    US: ticker로 직접 매칭
    KR: consolidated.name과 pm 내 KR name 부분일치
    """
    ticker = (holding.get("ticker") or "").strip().upper()
    name   = (holding.get("name") or "").strip()

    # US 종목: ticker == symbol
    if ticker in pm:
        return pm[ticker]

    # KR 종목: name 매칭 (pm 키가 "KR_NAME::{name}")
    kr_key = f"KR_NAME::{name}"
    if kr_key in pm:
        return pm[kr_key]

    # 부분 문자열 매칭 (KR 종목명이 약간 다를 경우 fallback)
    for key, entry in pm.items():
        if not key.startswith("KR_NAME::"):
            continue
        api_name = key.removeprefix("KR_NAME::")
        if name and name in api_name:
            return entry
        if api_name and api_name in name:
            return entry

    return None


# ──────────────────────────────────────────────
# JSON 업데이트
# ──────────────────────────────────────────────

def update_holdings(data: dict, pm: dict) -> tuple[dict, int, int]:
    """consolidated 섹션 업데이트. (updated_count, skipped_count) 반환"""
    updated = skipped = 0

    for h in data.get("consolidated", []):
        if is_manual(h):
            skipped += 1
            continue

        pos = find_position(h, pm)
        if pos is None:
            ticker = h.get("ticker", "?")
            print(f"  [WARN] {ticker} — 토스 API 응답에 없음 (매도됐거나 ticker 불일치)")
            skipped += 1
            continue

        new_val  = pos["value_krw"]
        new_cost = pos["cost_krw"] if pos["cost_krw"] > 0 else h.get("cost", 0)
        h["value"] = new_val
        h["cost"]  = new_cost
        h["pnl"]   = pos["pnl_krw"]
        h["ret"]   = pos["ret"]
        updated += 1

    # pnl 섹션도 동기화 (consolidated 복사본)
    data["pnl"] = [h for h in data.get("consolidated", []) if h.get("value", 0) > 0]

    return data, updated, skipped


def recalculate_aggregates(data: dict) -> dict:
    """kpi, subKpi, riskRatios, assetGroup, invGroup, top5, riskMonitor 재계산"""
    holdings = data.get("consolidated", [])

    # 보존값 (정적 데이터)
    re_val  = next((g["value"] for g in data.get("assetGroup", []) if g["name"] == "부동산"), 0)
    dep_val = next((g["value"] for g in data.get("assetGroup", []) if g["name"] == "보증금"), 0)
    bitcoin = data.get("subKpi", {}).get("비트코인", 0)
    debt    = data["kpi"]["총부채"]

    # 주식/ETF / 연금 / 현금 합산
    pension_total = sum(h["value"] for h in holdings if h.get("assetClass") == "퇴직연금")
    krw_cash      = next((h["value"] for h in holdings if h.get("ticker") == "TOSS_KRW_CASH"), 0)
    stock_total   = sum(
        h["value"] for h in holdings
        if h.get("assetClass") not in ("퇴직연금",) and h.get("ticker") != "TOSS_KRW_CASH"
    )

    total_asset  = re_val + dep_val + bitcoin + stock_total + pension_total + krw_cash
    net_worth    = total_asset - debt
    invest_asset = stock_total + pension_total

    # kpi
    data["kpi"].update({
        "총자산":   total_asset,
        "순자산":   net_worth,
        "투자자산": invest_asset,
        "부채비율": round(debt / total_asset, 8) if total_asset else 0,
    })

    # subKpi
    data["subKpi"].update({
        "현금성 자산":      krw_cash,
        "부동산+보증금":    re_val + dep_val,
        "비트코인":         bitcoin,
        "주식/ETF (통합)":  stock_total,
        "퇴직연금/예금":    pension_total,
    })

    # riskRatios
    lev_etf = sum(
        h["value"] for h in holdings
        if h.get("leverage") == "Y" and not is_manual(h)
    )
    data["riskRatios"].update({
        "부채/순자산":     round(debt    / net_worth, 8) if net_worth else 0,
        "위험자산/순자산": round((lev_etf + bitcoin) / net_worth, 8) if net_worth else 0,
        "레버리지/순자산": round(lev_etf  / net_worth, 8) if net_worth else 0,
        "비트코인/순자산": round(bitcoin  / net_worth, 8) if net_worth else 0,
    })

    # assetGroup value & pct 갱신
    group_map = {g["name"]: g for g in data.get("assetGroup", [])}
    if "주식/ETF" in group_map:
        group_map["주식/ETF"]["value"] = stock_total + pension_total
    for g in data.get("assetGroup", []):
        g["pct"] = round(g["value"] / total_asset, 10) if total_asset else 0

    # invGroup pct 갱신
    for g in data.get("invGroup", []):
        g["pct"] = round(g["value"] / invest_asset, 10) if invest_asset else 0

    # top5Concentration 갱신
    for t in data.get("top5Concentration", []):
        ticker = t.get("ticker")
        match  = next((h for h in holdings if h.get("ticker") == ticker), None)
        if match:
            t["value"] = match["value"]
            t["pct"]   = round(match["value"] / net_worth, 10) if net_worth else 0
            t["status"] = "과도" if t["pct"] >= 0.3 else ("주의" if t["pct"] >= 0.15 else "정상")

    # riskMonitor current 값 갱신
    sorted_h   = sorted(holdings, key=lambda h: h.get("value", 0), reverse=True)
    top1_value = sorted_h[0]["value"] if sorted_h else 0

    semi_semi_keywords = ["하이닉스", "반도체", "Semi", "AI반도체"]
    semi_exp = sum(
        h["value"] for h in holdings
        if any(kw in (h.get("name") or "") for kw in semi_semi_keywords)
        or any(kw.lower() in (h.get("ticker") or "").lower() for kw in ["SOXL"])
    )
    usd_assets = sum(
        h["value"] for h in holdings
        if "토스 해외" in (h.get("memo") or "")
    )

    risk_map = {
        "마이너스통장 / 순자산":        debt    / net_worth    if net_worth else 0,
        "마이너스통장 / 총자산":         debt    / total_asset  if total_asset else 0,
        "현금성 자산 / 월 생활비(₩3M)": krw_cash / 3_000_000,
        "현금성 자산 / 투자자산":        krw_cash / invest_asset if invest_asset else 0,
        "비트코인 / 순자산":             bitcoin  / net_worth   if net_worth else 0,
        "Top 1 단일 종목 / 순자산":     top1_value / net_worth if net_worth else 0,
        "레버리지 ETF / 순자산":         lev_etf  / net_worth   if net_worth else 0,
        "고위험자산 / 순자산":           (lev_etf + bitcoin) / net_worth if net_worth else 0,
        "주식+ETF+BTC / 순자산":        (stock_total + bitcoin) / net_worth if net_worth else 0,
        "반도체 익스포저 / 투자자산":    semi_exp / invest_asset if invest_asset else 0,
        "USD 자산 / 총자산":            usd_assets / total_asset if total_asset else 0,
        "해외 자산(USD+BTC) / 총자산":  (usd_assets + bitcoin) / total_asset if total_asset else 0,
    }
    for r in data.get("riskMonitor", []):
        item = r.get("item")
        if item in risk_map:
            r["current"] = round(risk_map[item], 10)
            # status 갱신
            threshold = r.get("threshold", 1)
            cat = r.get("category", "")
            if cat == "유동성":
                r["status"] = "정상" if r["current"] >= threshold else ("주의" if r["current"] >= threshold * 0.5 else "위험")
            else:
                r["status"] = "정상" if r["current"] < threshold * 0.7 else ("주의" if r["current"] < threshold else "위험")

    return data


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    now = datetime.now(KST)
    print(f"[{now.isoformat()}] 포트폴리오 업데이트 시작")

    # 1. 현재 JSON 로드
    with open(WEALTH_JSON, encoding="utf-8") as f:
        data = json.load(f)

    # 2. Toss API — 토큰 발급
    print("  토스증권 액세스 토큰 발급...")
    token = get_access_token()

    # 3. Toss API — 계좌 조회
    print("  계좌 조회...")
    account_seq = get_account_seq(token)
    print(f"    → accountSeq: {account_seq}")

    # 4. Toss API — 보유종목 조회 (KR + US 통합)
    print("  보유종목 조회 (국내 + 해외 통합)...")
    holdings_result = get_holdings(token, account_seq)
    items = holdings_result.get("items", [])
    kr_count = sum(1 for i in items if i.get("marketCountry") == "KR")
    us_count = sum(1 for i in items if i.get("marketCountry") == "US")
    print(f"    → 국내 {kr_count}개 / 해외 {us_count}개 / 합계 {len(items)}개")

    # 5. Toss API — 환율 조회
    print("  USD→KRW 환율 조회...")
    usd_to_krw = get_usd_to_krw(token)
    print(f"    → ₩{usd_to_krw:,.1f}/USD")

    # 6. 포지션 맵 빌드
    pm = build_position_map(holdings_result, usd_to_krw)

    # 7. Holdings 업데이트
    print("  Holdings 업데이트...")
    data, updated, skipped = update_holdings(data, pm)
    print(f"    → 업데이트 {updated}건 / 수동 유지 {skipped}건")

    # 8. 집계 재계산
    print("  집계 재계산...")
    data = recalculate_aggregates(data)

    # 9. 메타데이터 갱신
    data["asof"] = now.isoformat()
    if "_meta" not in data:
        data["_meta"] = {}
    data["_meta"]["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    data["_meta"]["builder"] = "scripts/sync_toss.py (GitHub Actions — 3hr auto)"
    data["_meta"]["usd_to_krw"] = usd_to_krw

    # 10. JSON 저장
    with open(WEALTH_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[{datetime.now(KST).isoformat()}] 완료 ✓")
    print(f"  순자산:      ₩{data['kpi']['순자산']:>15,.0f}")
    print(f"  총자산:      ₩{data['kpi']['총자산']:>15,.0f}")
    print(f"  주식/ETF:    ₩{data['subKpi']['주식/ETF (통합)']:>15,.0f}")
    print(f"  USD/KRW:     ₩{usd_to_krw:>9,.1f}")


if __name__ == "__main__":
    main()
