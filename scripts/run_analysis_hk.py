"""
ALUXE HK Sentiment Analysis Pipeline — v1.8
新增：每品牌獨立 Claude 分析，廣告不截斷
品牌：ALUXE HK、iprimo、銀座白石、Love Bird Diamond、Ragazza
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
    "iprimo": [
        "https://maps.app.goo.gl/zy5QPYQyxWwMTcdc9",
        "https://maps.app.goo.gl/uSLDjBvLmo72aXE1A",
        "https://maps.app.goo.gl/GWBu9uDQJbCw3YWc7",
        "https://maps.app.goo.gl/wS3Nk4ckNd2fWwSV6",
        "https://maps.app.goo.gl/7xdWNxhdCch6T2S87",
    ],
    "銀座白石": [
        "https://maps.app.goo.gl/Y2w8R5vFt4mHpGuh8",
        "https://maps.app.goo.gl/omjj7aAB2AKXVEhv5",
        "https://maps.app.goo.gl/ktWRKb7wskvuqkGF6",
    ],
    "ALUXE HK": [
        "https://maps.app.goo.gl/sNfXVbvuxRgVZKnn8",
        "https://maps.app.goo.gl/32fUqPG2BfUUpSAx6",
    ],
    "Love Bird Diamond": [
        "https://maps.app.goo.gl/M8SmVvgrLwoRLWBo7",
        "https://maps.app.goo.gl/E7GHo1upZLeqWUZM7",
    ],
    "Ragazza": [
        "https://maps.app.goo.gl/SK6EV7FBZ63JEpnd7",
    ],
}

# Meta 廣告 — FB 粉絲頁
ALL_FB_PAGES = [
    {"name": "iprimo",          "page_id": "100064850551818", "url": "https://www.facebook.com/iprimo.hk",             "own": False},
    {"name": "銀座白石",         "page_id": "100063840998738", "url": "https://www.facebook.com/diamondshiraishi.hk",   "own": False},
    {"name": "ALUXE HK",        "page_id": "100064941450204", "url": "https://www.facebook.com/aluxe.hk",              "own": True},
    {"name": "Love Bird Diamond","page_id": "100068555377282", "url": "https://www.facebook.com/lovebirddiamond",       "own": False},
    {"name": "Ragazza",         "page_id": "100063752931260", "url": "https://www.facebook.com/ragazzaita",            "own": False},
]

# Instagram 帳號（待補充）
IG_HANDLES_HK = [
    # "aluxe_hk",
    # "iprimo_hk",
]

# Threads 帳號
THREADS_HANDLES_HK = [
    "iprimohk",
    "ginzadiamond_hk",
    "aluxe_hk",
    "lovebird.diamond",
    "ragazza_diamond_official",
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
            ad_page_id = str(ad.get("page_id", ""))
            # 用 page_id 比對 ALL_FB_PAGES；找不到就維持 FB 原名、own=False
            brand_key = page_name
            own_flag = False
            for p in ALL_FB_PAGES:
                if str(p.get("page_id", "")) == ad_page_id:
                    brand_key = p["name"]
                    own_flag = p["own"]
                    break
            result.append({
                "brand": brand_key,
                "title": snap.get("title", ""),
                "body": body.get("text", "") if isinstance(body, dict) else str(body),
                "cta": snap.get("cta_text", ""),
                "platforms": ad.get("publisher_platform", []),
                "start_date": ad.get("start_date_formatted", ""),
                "is_active": ad.get("is_active", False),
                "own": own_flag,
                "_source": "Meta Ads Library",
                "_raw_ad": ad,
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
        items = apify_run("apify/threads-profile-api-scraper", {
            "usernames": THREADS_HANDLES_HK,
            "resultsType": "posts",
            "resultsLimit": 10,
        }, wait=60)
        for item in items:
            item["_source"] = "Threads"
            # 從帳號對應品牌
            username = item.get("username", "")
            brand_map = {
                "iprimohk": "iprimo",
                "ginzadiamond_hk": "銀座白石",
                "aluxe_hk": "ALUXE HK",
                "lovebird.diamond": "Love Bird Diamond",
                "ragazza_diamond_official": "Ragazza",
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

def claude_call_hk(prompt: str, max_tokens: int = 8192) -> dict:
    """共用 Claude API 呼叫函數"""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    if not r.ok:
        print(f"  [API錯誤] status={r.status_code}, body={r.text[:500]}")
        print(f"  [API錯誤] prompt長度={len(prompt)} 字元")
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def analyze_brand_hk(brand: str, reviews: list, ads: list, trends_text: str, own: bool) -> dict:
    """單一品牌深度分析（HK）"""
    brand_ads = json.dumps(ads, ensure_ascii=False)
    brand_reviews_text = json.dumps(reviews, ensure_ascii=False)
    own_label = "自家品牌" if own else "競品"

    prompt = f"""你是 ALUXE 珠寶品牌的 HK 市場行銷分析師。
針對【{brand}】（{own_label}）進行深度分析，輸出純 JSON（不含其他文字）：

分析重點：
1. 廣告策略：這個品牌目前在打什麼廣告？用什麼訴求、格式、優惠機制？
2. 顧客評論洞察：顧客最在意什麼？有什麼正面/負面主題？
3. {"ALUXE HK 可以如何從此品牌借鏡或做出差異化？" if not own else "ALUXE HK 自身的優勢是什麼？哪些地方可以強化？"}

請避免建議：員工個人IP、員工故事、社群人格化

{{
  "sentiment_score": 0.0-1.0,
  "positive_pct": 整數,
  "negative_pct": 整數,
  "neutral_pct": 整數,
  "review_count": 整數,
  "sources": ["Google Maps"],
  "top_themes": ["主題1","主題2","主題3"],
  "alert": null或"預警描述",
  "sample_positive": "留言原文或null",
  "sample_negative": "留言原文或null",
  "ad_count": 整數,
  "own": {"true" if own else "false"},
  "main_themes": ["廣告主題1","廣告主題2"],
  "cta_focus": "主要CTA方向",
  "platforms": ["FB","IG"],
  "key_offers": ["優惠1","優惠2"],
  "strategy_insight": "廣告策略洞察與{"ALUXE HK強化建議" if own else "ALUXE HK借鏡方向"}"
}}

評論資料（近14天）：{brand_reviews_text}
廣告資料：{brand_ads}
{"Google Trends HK：" + trends_text if trends_text and own else ""}"""

    result = claude_call_hk(prompt)
    result["brand"] = brand
    return result


def analyze_hk(reviews: list, ads: list, trends: list) -> dict:
    print("[Claude] HK 分析中（每品牌獨立分析）...")

    brand_reviews = defaultdict(list)
    for r in reviews:
        brand_reviews[r.get("_brand", "Unknown")].append(r)

    # 建立 page_name → 標準品牌名稱 的對應表
    page_to_brand = {}
    for p in ALL_FB_PAGES:
        page_to_brand[p["name"].lower()] = p["name"]
        page_to_brand[p["url"].split("/")[-1].lower()] = p["name"]

    def normalize_brand(page_name):
        key = page_name.lower()
        for k, v in page_to_brand.items():
            if k in key or key in k:
                return v
        return page_name

    ads_by_brand = defaultdict(list)
    ads_own = {}
    for ad in ads:
        raw_brand = ad.get("brand", "Unknown")
        brand = normalize_brand(raw_brand)
        ads_own[brand] = ad.get("own", False)
        ads_by_brand[brand].append({
            "title": ad.get("title", ""),
            "body": (ad.get("body") or "")[:150],
            "cta": ad.get("cta", ""),
            "platforms": ad.get("platforms", []),
            "start_date": ad.get("start_date", ""),
        })
    # 廣告分配：最新一半+最舊一半，總上限75則，剩餘配額補給其他品牌
    TOTAL_ADS = 75
    BASE_PER_BRAND = 15

    def pick_ads(ad_list, quota):
        if not ad_list: return []
        asc  = sorted(ad_list, key=lambda x: x.get("start_date",""))
        desc = sorted(ad_list, key=lambda x: x.get("start_date",""), reverse=True)
        half = quota // 2
        newest = desc[:half]
        oldest = asc[:quota - half]
        seen = {id(a) for a in newest}
        return (newest + [a for a in oldest if id(a) not in seen])[:quota]

    brands_with_ads = [b for b in ads_by_brand if ads_by_brand[b]]
    allocated = {b: min(len(ads_by_brand[b]), BASE_PER_BRAND) for b in brands_with_ads}
    leftover  = TOTAL_ADS - sum(allocated.values())
    while leftover > 0:
        can_more = [b for b in brands_with_ads if len(ads_by_brand[b]) > allocated[b]]
        if not can_more: break
        give = max(1, leftover // len(can_more))
        for b in can_more:
            add = min(give, len(ads_by_brand[b]) - allocated[b], leftover)
            allocated[b] += add
            leftover -= add
            if leftover == 0: break
    for b in ads_by_brand:
        ads_by_brand[b] = pick_ads(ads_by_brand[b], allocated.get(b, BASE_PER_BRAND))

    # Debug：確認廣告分配結果
    total_after = sum(len(v) for v in ads_by_brand.values())
    print(f"  [廣告分配] 分配後總數：{total_after} 則")
    for b, alist in ads_by_brand.items():
        print(f"    {b}：{len(alist)} 則")

    trends_text = json.dumps(trends[:5], ensure_ascii=False) if trends else ""
    all_brands = list(GOOGLE_MAPS_BRAND_URLS.keys())
    brand_results = {}

    for brand in all_brands:
        print(f"  [Claude] 分析 {brand}...")
        b_reviews = brand_reviews.get(brand, [])[:20]
        # 模糊匹配廣告品牌（因 page_name 可能與標準名稱略有不同）
        b_ads = ads_by_brand.get(brand, [])
        if not b_ads:
            for k in ads_by_brand:
                if brand.lower() in k.lower() or k.lower() in brand.lower():
                    b_ads = ads_by_brand[k]
                    break
        b_ads = b_ads[:15]  # 硬性上限，每品牌最多 15 則
        own = "ALUXE" in brand
        try:
            result = analyze_brand_hk(brand, b_reviews, b_ads, trends_text, own)
            brand_results[brand] = result
        except Exception as e:
            print(f"  [Claude] {brand} 分析失敗：{e}")
            brand_results[brand] = {
                "sentiment_score": 0, "positive_pct": 0, "negative_pct": 0,
                "neutral_pct": 0, "review_count": 0, "sources": ["Google Maps"],
                "top_themes": [], "alert": None, "sample_positive": None, "sample_negative": None,
                "ad_count": len(b_ads), "own": own, "main_themes": [], "cta_focus": "",
                "platforms": [], "key_offers": [], "strategy_insight": ""
            }

    # 整體摘要
    print("  [Claude] 產出整體摘要...")
    brand_list = "、".join(all_brands)
    summary_data = {b: {k: v for k, v in r.items() if k in ["strategy_insight", "top_themes", "cta_focus", "key_offers", "alert"]}
                    for b, r in brand_results.items()}
    summary_prompt = f"""你是 ALUXE 珠寶品牌的 HK 市場行銷分析師。
根據以下各品牌分析結果，輸出純 JSON（不含其他文字）：

請避免建議：員工個人IP、員工故事、社群人格化

{{
  "summary": "2-3句整體觀察，聚焦競品策略動態與 ALUXE HK 可學習的方向",
  "competitor_alerts": [{{"brand":"","issue":"","severity":1-5,"opportunity":""}}],
  "hot_topics": [{{"topic":"","volume":"high/medium/low","actionable":true,"suggestion":"具體的廣告切角或部落格主題建議"}}],
  "market_trends": [{{"keyword":"","trend":"rising/stable/falling","insight":"對 ALUXE HK 廣告或內容策略的意義"}}],
  "actionable_top3": [
    "🎯 本週可學習的競品廣告動作：（具體說明哪個品牌做了什麼、ALUXE HK 可以怎麼借鏡）",
    "📝 內容行銷機會：（具體的部落格主題或廣告素材切角）",
    "🔍 市場趨勢機會：（從 Trends 找到的 HK 市場可優化方向）"
  ]
}}

各品牌分析摘要：{json.dumps(summary_data, ensure_ascii=False)}
Google Trends HK：{trends_text}"""

    summary_result = claude_call_hk(summary_prompt)

    result = {
        "summary": summary_result.get("summary", ""),
        "brands": {b: {k: v for k, v in r.items() if k in [
            "sentiment_score", "positive_pct", "negative_pct", "neutral_pct",
            "review_count", "sources", "top_themes", "alert", "sample_positive", "sample_negative"
        ]} for b, r in brand_results.items()},
        "competitor_ads": {b: {k: v for k, v in r.items() if k in [
            "ad_count", "own", "main_themes", "cta_focus", "platforms", "key_offers", "strategy_insight"
        ]} for b, r in brand_results.items()},
        "competitor_alerts": summary_result.get("competitor_alerts", []),
        "hot_topics": summary_result.get("hot_topics", []),
        "market_trends": summary_result.get("market_trends", []),
        "actionable_top3": summary_result.get("actionable_top3", []),
        "generated_at": datetime.datetime.utcnow().isoformat(),
        "market": "HK"
    }
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
    own  = [v["sentiment_score"] for k,v in report["brands"].items() if "ALUXE" in k and v.get("sentiment_score") is not None]
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
# PART 5.5 — Raw Data 儲存（新增）
# ══════════════════════════════════════════════════════

def get_iso_week(d=None):
    """回傳 ISO 週次字串，例如 '2026-W17'"""
    if d is None:
        d = datetime.date.today()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def sheets_read_column(tok, sheet, col, start_row=2, limit=5000):
    """讀取指定分頁某一欄的值，用來去重比對。"""
    rng = f"{sheet}!{col}{start_row}:{col}"
    try:
        r = requests.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_ID}/values/{rng}",
            headers={"Authorization": f"Bearer {tok}"},
            timeout=30,
        )
        r.raise_for_status()
        values = r.json().get("values", [])
        flat = [row[0] for row in values if row and row[0]]
        return flat[-limit:] if len(flat) > limit else flat
    except Exception as e:
        print(f"  [Raw Data] read column failed: {e}")
        return []


def save_raw_googlemaps(tok, reviews, market):
    """把 Google Maps 評論寫入 Raw Data_GoogleMaps 分頁（去重後）。"""
    print(f"[Raw Data] GoogleMaps {market} writing...")
    gm_reviews = [r for r in reviews if r.get("_source") == "Google Maps"]
    if not gm_reviews:
        print(f"  -> no Google Maps data, skip")
        return
    existing_urls = set(sheets_read_column(tok, "Raw Data_GoogleMaps", "I", limit=5000))
    print(f"  -> existing review_url: {len(existing_urls)} rows")
    scrape_date = datetime.date.today().isoformat()
    week = get_iso_week()
    new_rows = []
    skipped = 0
    for r in gm_reviews:
        review_url = r.get("reviewUrl", "")
        if not review_url:
            continue
        if review_url in existing_urls:
            skipped += 1
            continue
        row = [
            scrape_date, week, market, r.get("_brand", ""),
            r.get("title", ""), r.get("name", ""), r.get("stars", ""),
            r.get("text", ""), review_url, r.get("url", ""),
            json.dumps(r, ensure_ascii=False),
        ]
        new_rows.append(row)
        existing_urls.add(review_url)
    if new_rows:
        batch_size = 500
        for i in range(0, len(new_rows), batch_size):
            batch = new_rows[i:i + batch_size]
            sheets_append(tok, "Raw Data_GoogleMaps", batch)
        print(f"  -> wrote {len(new_rows)} new reviews (skipped {skipped} duplicates)")
    else:
        print(f"  -> no new reviews ({skipped} duplicates)")


def save_raw_metaads(tok, ads, market):
    """把 Meta 廣告寫入 Raw Data_MetaAds 分頁（去重後）。
    讀取 _raw_ad 欄位（原始 Apify 資料），再從中提取欄位。"""
    print(f"[Raw Data] MetaAds {market} writing...")
    if not ads:
        print(f"  -> no ads data, skip")
        return
    existing_ids = set(sheets_read_column(tok, "Raw Data_MetaAds", "F", limit=5000))
    print(f"  -> existing ad_id: {len(existing_ids)} rows")
    scrape_date = datetime.date.today().isoformat()
    week = get_iso_week()
    # 對照表改用 page_id 當 key（比 page_name 更精準）
    page_info = {str(p.get("page_id", "")): {"brand": p["name"], "is_own": p.get("own", False)}
                 for p in ALL_FB_PAGES if p.get("page_id")}
    new_rows = []
    skipped = 0
    no_raw = 0
    for ad in ads:
        raw = ad.get("_raw_ad")
        if raw is None:
            no_raw += 1
            continue
        ad_id = str(raw.get("ad_archive_id", ""))
        if not ad_id:
            continue
        if ad_id in existing_ids:
            skipped += 1
            continue
        snap = raw.get("snapshot", {}) or {}
        body = snap.get("body", {})
        body_text = body.get("text", "") if isinstance(body, dict) else str(body or "")
        platforms = raw.get("publisher_platform", [])
        platforms_str = ", ".join(platforms) if isinstance(platforms, list) else str(platforms)
        page_name = raw.get("page_name", snap.get("page_name", ""))
        raw_page_id = str(raw.get("page_id", ""))
        info = page_info.get(raw_page_id, {"brand": page_name, "is_own": False})
        row = [
            scrape_date, week, market, info["brand"], info["is_own"],
            ad_id, page_name, snap.get("title", ""), body_text,
            snap.get("cta_text", ""), snap.get("link_description", ""),
            snap.get("display_format", ""), raw.get("start_date_formatted", ""),
            raw.get("end_date_formatted", ""), raw.get("is_active", ""),
            platforms_str, raw.get("ad_library_url", ""),
            json.dumps(raw, ensure_ascii=False),
        ]
        new_rows.append(row)
        existing_ids.add(ad_id)
    if new_rows:
        batch_size = 500
        for i in range(0, len(new_rows), batch_size):
            batch = new_rows[i:i + batch_size]
            sheets_append(tok, "Raw Data_MetaAds", batch)
        print(f"  -> wrote {len(new_rows)} new ads (skipped {skipped} duplicates, {no_raw} no _raw_ad)")
    else:
        print(f"  -> no new ads ({skipped} duplicates, {no_raw} no _raw_ad)")


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

    # 廣告摘要：分自家 + 競品兩段
    own_ads_summary = ""
    comp_ads_summary = ""
    for brand, ad_data in report.get("competitor_ads",{}).items():
        count = ad_data.get("ad_count", 0)
        focus = ad_data.get("cta_focus","")
        line = f"\n· {brand}：{count} 則廣告，主打「{focus}」"
        if ad_data.get("own", False):
            own_ads_summary += line
        else:
            comp_ads_summary += line
    own_ads_section = f"自家廣告動態{own_ads_summary}\n\n" if own_ads_summary else ""
    comp_ads_section = f"競品廣告動態{comp_ads_summary}\n\n" if comp_ads_summary else ""

    msg = (f"🇭🇰 ALUXE HK 輿情週報 · {date}\n\n"
           f"{icon} 自家品牌平均分：{avg}\n\n"
           f"競品負評預警\n{alerts}\n\n"
           f"{own_ads_section}"
           f"{comp_ads_section}"
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

    # ★ 新增：保存 Raw Data（在分析之前先存，避免分析失敗就沒資料）
    raw_tok = sheets_token()
    save_raw_googlemaps(raw_tok, reviews, market="HK")
    save_raw_metaads(raw_tok, ads, market="HK")

    report  = analyze_hk(all_reviews, ads, trends)

    save_json_hk(report)
    write_sheets_hk(report)
    send_telegram_hk(report)

    print(f"\n✅ HK 完成")
    print(f"   試算表：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")


if __name__ == "__main__":
    main()
