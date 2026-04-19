"""
ALUXE HK Sentiment Analysis Pipeline — v1.3
修正：Sheets 工作表名稱統一為 _HK 後綴格式
品牌：ALUXE HK、iprimo、銀作白石、Diabond、Love Bird Diamond、Ragazza、Futago Bridal
"""

import os, json, datetime, base64, requests, time
from pathlib import Path
from collections import defaultdict

APIFY_TOKEN       = os.environ["APIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
SHEETS_ID         = os.environ["GOOGLE_SHEETS_ID"]
SERVICE_ACCOUNT   = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
PAGES_URL         = os.environ.get("PAGES_URL", "https://aluxejulia.github.io/aluxe-sentiment/")

# ── HK 品牌設定 ───────────────────────────────────────

GOOGLE_MAPS_BRAND_URLS = {
    "ALUXE HK": [
        "https://maps.app.goo.gl/sNfXVbvuxRgVZKnn8",
        "https://maps.app.goo.gl/32fUqPG2BfUUpSAx6",
    ],
    "iprimo": [
        "https://maps.app.goo.gl/zy5QPYQyxWwMTcdc9",
        "https://maps.app.goo.gl/uSLDjBvLmo72aXE1A",
        "https://maps.app.goo.gl/GWBu9uDQJbCw3YWc7",
        "https://maps.app.goo.gl/wS3Nk4ckNd2fWwSV6",
        "https://maps.app.goo.gl/7xdWNxhdCch6T2S87",
    ],
    "銀作白石": [
        "https://maps.app.goo.gl/Y2w8R5vFt4mHpGuh8",
        "https://maps.app.goo.gl/omjj7aAB2AKXVEhv5",
        "https://maps.app.goo.gl/ktWRKb7wskvuqkGF6",
    ],
    "Diabond": [
        "https://maps.app.goo.gl/9ghwinLHyDJHuQ9ZA",
    ],
    "Love Bird Diamond": [
        "https://maps.app.goo.gl/M8SmVvgrLwoRLWBo7",
        "https://maps.app.goo.gl/E7GHo1upZLeqWUZM7",
    ],
    "Ragazza": [
        "https://maps.app.goo.gl/SK6EV7FBZ63JEpnd7",
    ],
    "Futago Bridal": [
        "https://maps.app.goo.gl/KUU8ryZWoV7nBdms7",
    ],
}

# Meta 廣告 — FB 粉絲頁
ALL_FB_PAGES = [
    {"name": "ALUXE HK",        "url": "https://www.facebook.com/aluxe.hk",              "own": True},
    {"name": "iprimo",          "url": "https://www.facebook.com/iprimo.hk",             "own": False},
    {"name": "銀作白石",         "url": "https://www.facebook.com/diamondshiraishi.hk",   "own": False},
    {"name": "Diabond",         "url": "https://www.facebook.com/diabondhk",             "own": False},
    {"name": "Love Bird Diamond","url": "https://www.facebook.com/lovebirddiamond",       "own": False},
    {"name": "Ragazza",         "url": "https://www.facebook.com/ragazzaita",            "own": False},
    {"name": "Futago Bridal",   "url": "https://www.facebook.com/futagobridal",          "own": False},
]

# Instagram 帳號（待補充）
IG_HANDLES_HK = [
    # "aluxe_hk",
    # "iprimo_hk",
]

# Threads 帳號
THREADS_HANDLES_HK = [
    "aluxe_hk",
    "iprimohk",
    "ginzadiamond_hk",
    "diabondhk",
    "lovebird.diamond",
    "ragazza_diamond_official",
    "futago_bridal_hk",
]

TREND_KEYWORDS_HK = [
    "engagement ring Hong Kong",
    "lab grown diamond Hong Kong",
    "wedding ring customisation Hong Kong",
    "bespoke engagement ring Hong Kong",
    "iprimo Hong Kong",
    "ALUXE Hong Kong",
]

OUTPUT_DIR = Path("docs")
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Google Auth ───────────────────────────────────────

def google_token(scope: str) -> str:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    def b64(s): return base64.urlsafe_b64encode(s).rstrip(b"=").decode()

    now    = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim  = b64(json.dumps({
        "iss": SERVICE_ACCOUNT["client_email"], "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600, "iat": now,
    }).encode())

    key = serialization.load_pem_private_key(
        SERVICE_ACCOUNT["private_key"].encode(), password=None,
        backend=default_backend())
    sig = b64(key.sign(f"{header}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256()))

    resp = requests.post("https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion": f"{header}.{claim}.{sig}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Apify 通用執行 ────────────────────────────────────

def apify_run(actor_id: str, payload: dict, wait: int = 120) -> list:
    url = (f"https://api.apify.com/v2/acts/{actor_id}/runs"
           f"?token={APIFY_TOKEN}&waitForFinish={wait}")
    r = requests.post(url, json=payload, timeout=wait + 60)
    r.raise_for_status()
    ds = r.json()["data"]["defaultDatasetId"]
    items = requests.get(
        f"https://api.apify.com/v2/datasets/{ds}/items?token={APIFY_TOKEN}&clean=true",
        timeout=60).json()
    return items if isinstance(items, list) else []


# ══════════════════════════════════════════════════════
# PART 1 — Google Maps 評論
# ══════════════════════════════════════════════════════

def fetch_reviews_hk() -> list:
    print("[Apify] HK Google Maps 評論（近 14 天，按品牌合併）...")
    all_reviews = []
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=14)

    for brand, urls in GOOGLE_MAPS_BRAND_URLS.items():
        if not urls:
            continue
        try:
            items = apify_run("compass~google-maps-reviews-scraper", {
                "startUrls": [{"url": u} for u in urls],
                "maxReviews": 30,
                "language": "en",
                "reviewsSort": "newest",
            })
            for item in items:
                item["_source"] = "Google Maps"
                item["_brand"]  = brand
                pub_date = item.get("publishedAtDate") or item.get("date") or ""
                if pub_date:
                    try:
                        dt = datetime.datetime.fromisoformat(pub_date[:19])
                        if dt >= cutoff:
                            all_reviews.append(item)
                    except Exception:
                        all_reviews.append(item)
                else:
                    all_reviews.append(item)
            print(f"  -> {brand}：{len(items)} 筆")
        except Exception as e:
            print(f"  -> {brand} 失敗：{e}")

    print(f"  -> 合計 {len(all_reviews)} 筆（近 14 天）")
    return all_reviews


# ══════════════════════════════════════════════════════
# PART 2 — Meta 廣告（待 FB 頁面補充後啟用）
# ══════════════════════════════════════════════════════

def fetch_meta_ads_hk() -> list:
    if not ALL_FB_PAGES:
        print("[Meta Ads] HK FB 頁面未設定，略過")
        return []
    print("[Meta Ads] HK 抓取廣告...")
    try:
        ads = apify_run("curious_coder~facebook-ads-library-scraper", {
            "urls": [{"url": p["url"]} for p in ALL_FB_PAGES],
            "limitPerSource": 8,
            "scrapePageAds-dot-activeStatus": "active",
            "scrapePageAds-dot-period": "last30d",
            "scrapePageAds-dot-sortBy": "most_recent",
        }, wait=120)
        result = []
        for ad in ads:
            snap = ad.get("snapshot", {})
            body = snap.get("body", {})
            page_name = ad.get("page_name", "")
            own_flag = any(p["own"] for p in ALL_FB_PAGES if p["name"] in page_name or page_name in p["name"])
            result.append({
                "brand": page_name,
                "title": snap.get("title", ""),
                "body": body.get("text", "") if isinstance(body, dict) else str(body),
                "cta": snap.get("cta_text", ""),
                "platforms": ad.get("publisher_platform", []),
                "start_date": ad.get("start_date_formatted", ""),
                "is_active": ad.get("is_active", False),
                "own": own_flag,
                "_source": "Meta Ads Library",
            })
        print(f"  -> {len(result)} 則廣告")
        return result
    except Exception as e:
        print(f"  [Meta Ads] 失敗：{e}")
        return []


# ══════════════════════════════════════════════════════
# PART 2b — Threads 留言
# ══════════════════════════════════════════════════════

def fetch_threads_hk() -> list:
    if not THREADS_HANDLES_HK:
        print("[Threads] HK 帳號未設定，略過")
        return []
    print("[Apify] HK Threads 貼文留言...")
    try:
        items = apify_run("apify/threads-scraper", {
            "usernames": THREADS_HANDLES_HK,
            "resultsLimit": 10,
        }, wait=60)
        for item in items:
            item["_source"] = "Threads"
            # 從帳號對應品牌
            username = item.get("username", "")
            brand_map = {
                "aluxe_hk": "ALUXE HK",
                "iprimohk": "iprimo",
                "ginzadiamond_hk": "銀作白石",
                "diabondhk": "Diabond",
                "lovebird.diamond": "Love Bird Diamond",
                "ragazza_diamond_official": "Ragazza",
                "futago_bridal_hk": "Futago Bridal",
            }
            item["_brand"] = brand_map.get(username, username)
        print(f"  -> {len(items)} 則 Threads 貼文")
        return items
    except Exception as e:
        print(f"  [Threads] 失敗：{e}")
        return []


# ══════════════════════════════════════════════════════
# PART 3 — Google Trends HK
# ══════════════════════════════════════════════════════

def fetch_trends_hk() -> list:
    print("[Trends] HK 市場趨勢...")
    try:
        items = apify_run("apify~google-trends-scraper", {
            "searchTerms": TREND_KEYWORDS_HK,
            "geo": "HK",
            "timeRange": "today 1-m",
        }, wait=90)
        print(f"  -> {len(items)} 筆")
        return items
    except Exception as e:
        print(f"  [Trends] 失敗：{e}")
        return []


# ══════════════════════════════════════════════════════
# PART 4 — Claude 分析
# ══════════════════════════════════════════════════════

def analyze_hk(reviews: list, ads: list, trends: list) -> dict:
    print("[Claude] HK 分析中...")

    ads_by_brand = {}
    for ad in ads:
        brand = ad.get("brand", "Unknown")
        if brand not in ads_by_brand:
            ads_by_brand[brand] = {"own": ad.get("own", False), "ads": []}
        ads_by_brand[brand]["ads"].append({
            "title": ad.get("title", ""),
            "body": (ad.get("body") or "")[:200],
            "cta": ad.get("cta", ""),
            "platforms": ad.get("platforms", []),
        })

    ads_summary  = json.dumps(ads_by_brand, ensure_ascii=False)
    trends_text  = json.dumps(trends[:5], ensure_ascii=False) if trends else ""

    # 每品牌各取最新 20 則
    brand_reviews = defaultdict(list)
    for r in reviews:
        brand_reviews[r.get("_brand", "Unknown")].append(r)
    sampled = []
    for bl in brand_reviews.values():
        sampled.extend(bl[:20])

    brand_list = "、".join(GOOGLE_MAPS_BRAND_URLS.keys())

    prompt = f"""你是 ALUXE 珠寶品牌的 HK 市場行銷分析師。
分析以下資料，輸出純 JSON（不含其他文字）：

{{
  "summary": "2-3句整體觀察，整合評論、廣告、趨勢洞察",
  "brands": {{
    "品牌名": {{
      "sentiment_score": 0.0-1.0,
      "positive_pct": 整數,
      "negative_pct": 整數,
      "neutral_pct": 整數,
      "review_count": 整數,
      "sources": ["Google Maps"],
      "top_themes": ["主題1","主題2","主題3"],
      "alert": null或"預警描述",
      "sample_positive": "留言原文或null",
      "sample_negative": "留言原文或null"
    }}
  }},
  "competitor_alerts": [{{"brand":"","issue":"","severity":1-5,"opportunity":""}}],
  "competitor_ads": {{
    "品牌名": {{
      "ad_count": 整數,
      "own": true或false,
      "main_themes": ["廣告主題1","廣告主題2"],
      "cta_focus": "主要CTA方向",
      "platforms": ["FB","IG"],
      "key_offers": ["優惠1","優惠2"],
      "strategy_insight": "廣告策略洞察"
    }}
  }},
  "hot_topics": [{{"topic":"","volume":"high/medium/low","actionable":true,"suggestion":""}}],
  "market_trends": [{{"keyword":"","trend":"rising/stable/falling","insight":"市場意義"}}],
  "actionable_top3": ["行動1","行動2","行動3"]
}}

重要：brands 欄位請合併成七個品牌（{brand_list}），不要拆成個別分店。
評論資料（近 14 天）：{json.dumps(sampled, ensure_ascii=False)}
競品 Meta 廣告資料：{ads_summary}
Google Trends HK：{trends_text}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": 8192,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw.strip())
    result["generated_at"] = datetime.datetime.utcnow().isoformat()
    result["market"] = "HK"
    print("[Claude] HK 完成")
    return result


# ══════════════════════════════════════════════════════
# PART 5 — Google Sheets（寫入 HK 專屬分頁）
# ══════════════════════════════════════════════════════

def sheets_token() -> str:
    return google_token("https://www.googleapis.com/auth/spreadsheets")

def sheets_append(tok, sheet, rows):
    requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_ID}"
        f"/values/{sheet}!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        headers={"Authorization": f"Bearer {tok}"},
        json={"values": rows}, timeout=30).raise_for_status()

def sheets_update(tok, sheet, rng, rows):
    requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_ID}"
        f"/values/{sheet}!{rng}?valueInputOption=USER_ENTERED",
        headers={"Authorization": f"Bearer {tok}"},
        json={"values": rows}, timeout=30).raise_for_status()

def write_sheets_hk(report: dict):
    print("[Sheets] HK 寫入中...")
    tok  = sheets_token()
    date = report["generated_at"][:10]
    own  = [v["sentiment_score"] for k,v in report["brands"].items() if "ALUXE" in k]
    avg  = round(sum(own)/len(own), 2) if own else 0

    # HK Dashboard
    rows = [["ALUXE HK 輿情監控","","","","","","",""],
            ["最後更新", date, "自家品牌平均分", avg,"","","",""],
            ["","","","","","","",""],
            ["品牌","情感分","正面%","負面%","中性%","評論數","來源","預警"]]
    for n,d in report["brands"].items():
        rows.append([n, d.get("sentiment_score",""), d.get("positive_pct",""),
                     d.get("negative_pct",""), d.get("neutral_pct",""),
                     d.get("review_count",""), ", ".join(d.get("sources",[])),
                     d.get("alert") or "—"])
    sheets_update(tok, "Dashboard_HK", "A1", rows)

    # HK Weekly History
    for n,d in report["brands"].items():
        sheets_append(tok, "Weekly History_HK", [[
            date, n, d.get("sentiment_score",""), d.get("positive_pct",""),
            d.get("negative_pct",""), d.get("neutral_pct",""), d.get("review_count",""),
            ", ".join(d.get("sources",[])), ", ".join(d.get("top_themes",[])),
            d.get("alert") or ""]])

    # HK Competitor Alerts
    for a in report.get("competitor_alerts",[]):
        sheets_append(tok, "Competitor Alerts_HK", [[
            date, a.get("brand",""), a.get("severity",""),
            a.get("issue",""), a.get("opportunity","")]])

    # HK Hot Topics
    for t in report.get("hot_topics",[]):
        sheets_append(tok, "Hot Topics_HK", [[
            date, t.get("topic",""), t.get("volume",""),
            "是" if t.get("actionable") else "否", t.get("suggestion","")]])

    # HK Action Log
    for i,a in enumerate(report.get("actionable_top3",[]), 1):
        sheets_append(tok, "Action Log_HK", [[date, f"優先 {i}", a, "待執行"]])

    # HK Competitor Ads
    for brand, ad_data in report.get("competitor_ads",{}).items():
        sheets_append(tok, "Competitor Ads_HK", [[
            date, brand,
            ad_data.get("ad_count",""),
            ", ".join(ad_data.get("main_themes",[])),
            ad_data.get("cta_focus",""),
            ", ".join(ad_data.get("platforms",[])),
            ", ".join(ad_data.get("key_offers",[])),
            ad_data.get("strategy_insight",""),
        ]])

    print("[Sheets] HK 完成（6 個工作表）")


# ══════════════════════════════════════════════════════
# PART 6 — HTML（寫入 docs/hk.json，由 SG 儀表板讀取）
# ══════════════════════════════════════════════════════

def save_json_hk(report: dict):
    hf = OUTPUT_DIR / "hk_history.json"
    history = json.loads(hf.read_text()) if hf.exists() else []
    history.insert(0, report)
    hf.write_text(json.dumps(history[:26], ensure_ascii=False, indent=2))
    (OUTPUT_DIR / "hk_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("[JSON] HK 完成")


# ══════════════════════════════════════════════════════
# PART 7 — Telegram
# ══════════════════════════════════════════════════════

def send_telegram_hk(report: dict):
    date = report.get("generated_at","")[:10]
    own  = [v["sentiment_score"] for k,v in report.get("brands",{}).items() if "ALUXE" in k]
    avg  = round(sum(own)/len(own),2) if own else 0
    icon = "📈" if avg>=0.7 else "📊" if avg>=0.5 else "📉"

    alerts = "\n".join(
        f"{'🔴' if a.get('severity',1)>=4 else '🟡'} {a['brand']} — {a['issue']}"
        for a in report.get("competitor_alerts",[])) or "本週無預警"

    actions = "\n".join(f"{i+1}. {a}" for i,a in enumerate(report.get("actionable_top3",[])))

    msg = (f"🇭🇰 ALUXE HK 輿情週報 · {date}\n\n"
           f"{icon} 自家品牌平均分：{avg}\n\n"
           f"競品負評預警\n{alerts}\n\n"
           f"本週優先行動\n{actions}\n\n"
           f"儀表板：{PAGES_URL}")

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=30,
    ).raise_for_status()
    print("[Telegram] HK 完成")


# ══════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════

def main():
    print(f"\n{'='*52}")
    print(f"  ALUXE HK Sentiment v1.0  —  {datetime.date.today()}")
    print(f"{'='*52}\n")

    reviews = fetch_reviews_hk()
    threads = fetch_threads_hk()
    ads     = fetch_meta_ads_hk()
    trends  = fetch_trends_hk()

    all_reviews = reviews + threads
    report  = analyze_hk(all_reviews, ads, trends)

    save_json_hk(report)
    write_sheets_hk(report)
    send_telegram_hk(report)

    print(f"\n✅ HK 完成")
    print(f"   試算表：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")


if __name__ == "__main__":
    main()
