"""
ALUXE SG Sentiment Analysis Pipeline — v5.2
修正：Sheets 工作表名稱加上 _SG 後綴
"""

import os, json, datetime, base64, requests, time
from pathlib import Path

APIFY_TOKEN       = os.environ["APIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
SHEETS_ID         = os.environ["GOOGLE_SHEETS_ID"]
SERVICE_ACCOUNT   = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
PAGES_URL         = os.environ.get("PAGES_URL", "https://aluxejulia.github.io/aluxe-sentiment/")
GSC_SITE          = "https://www.aluxe.com/"

BRANDS_OWN  = ["ALUXE Singapore"]
BRANDS_COMP = ["Jannpaul Singapore", "Michael Trio Singapore",
               "Love and Co Singapore", "Lee Hwa Jewellery Singapore"]
ALL_BRANDS  = BRANDS_OWN + BRANDS_COMP

# Google Maps 直接 URL — 按品牌分組，評論會合併成一個品牌分析
GOOGLE_MAPS_BRAND_URLS = {
    "ALUXE": [
        "https://maps.app.goo.gl/cCqEKDN2vqZtVQf18",
        "https://maps.app.goo.gl/VyVSAH5EjgRPnmvr9",
        "https://maps.app.goo.gl/k2K5N3GqJKbpQDHR9",
        "https://maps.app.goo.gl/2G7eofCsgbSvZdJu7",
    ],
    "Jannpaul": [
        "https://maps.app.goo.gl/B2EYasv2BDix3R367",
    ],
    "Michael Trio": [
        "https://maps.app.goo.gl/hbhPERnTgPwdZKkG9",
        "https://maps.app.goo.gl/29774jBhhBjwGTLk7",
        "https://maps.app.goo.gl/ZcGpK71vmdu14PYL8",
        "https://maps.app.goo.gl/Aocv1zUR5PXUtL548",
    ],
    "Lee Hwa": [
        "https://maps.app.goo.gl/PJ2wWcobcMNkErSi6",
        "https://maps.app.goo.gl/pe589DPa148YET7n7",
        "https://maps.app.goo.gl/ZWSzbtH1F5czTPYt7",
        "https://maps.app.goo.gl/cfWLYNKXCqWLUFfW9",
        "https://maps.app.goo.gl/yeJQMBQK5YkWadsN7",
    ],
    "Love & Co": [
        "https://maps.app.goo.gl/LkvPmag6HzEocwwN8",
        "https://maps.app.goo.gl/SuVcfMhdPc5QFhnK7",
        "https://maps.app.goo.gl/NSroqW89T3hy3uhG6",
        "https://maps.app.goo.gl/sYu7kNJed3iGREqN6",
    ],
}
# 展開成帶 brand 標記的 URL 列表
GOOGLE_MAPS_URLS = [
    {"url": url, "brand": brand}
    for brand, urls in GOOGLE_MAPS_BRAND_URLS.items()
    for url in urls
]

# Meta 廣告資料庫 — 自家 + 競品粉絲頁
ALL_FB_PAGES = [
    {"name": "ALUXE SG",    "url": "https://www.facebook.com/aluxe.sg",         "own": True},
    {"name": "Jannpaul",    "url": "https://www.facebook.com/JANNPAULDiamonds", "own": False},
    {"name": "Michael Trio","url": "https://www.facebook.com/michaeltriojewels", "own": False},
    {"name": "Love & Co",   "url": "https://www.facebook.com/L0veandC0",         "own": False},
    {"name": "Lee Hwa",     "url": "https://www.facebook.com/LeeHwaJewellery",   "own": False},
]

# Instagram 帳號 — 最新貼文 URL 抓留言
IG_HANDLES = ["aluxe_sg", "jannpaul", "michaeltrio", "loveandcoofficial", "leehwajewellery"]

TREND_KEYWORDS = [
    "engagement ring Singapore",
    "lab grown diamond Singapore",
    "wedding ring customisation Singapore",
    "bespoke engagement ring Singapore",
    "Jannpaul", "Michael Trio", "ALUXE Singapore",
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
# PART 1 — 評論資料抓取
# ══════════════════════════════════════════════════════

def fetch_reviews() -> list:
    print("[Apify] Google Maps 評論（直接 URL，近 14 天，按品牌合併）...")
    if not GOOGLE_MAPS_URLS:
        print("  -> 無 URL，略過")
        return []

    # 按品牌分批抓，確保每則評論都有品牌標記
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
                item["_brand"] = brand  # 強制標記品牌
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


def fetch_instagram() -> list:
    print("[Apify] Instagram 最新貼文留言...")
    try:
        # 直接用 username 模式，不需要先抓貼文 URL
        comments = apify_run("apify/instagram-comment-scraper", {
            "directUrls": [f"https://www.instagram.com/{h}/" for h in IG_HANDLES],
            "resultsLimit": 20,
        }, wait=30)

        for c in comments:
            c["_source"] = "Instagram"
        print(f"  -> {len(comments)} 則留言")
        return comments
    except Exception as e:
        print(f"  [Instagram] 失敗：{e}")
        return []


# ══════════════════════════════════════════════════════
# PART 2 — Meta 廣告監控
# ══════════════════════════════════════════════════════

def fetch_meta_ads() -> list:
    print("[Meta Ads] 抓取自家 + 競品廣告...")
    try:
        ads = apify_run("curious_coder~facebook-ads-library-scraper", {
            "urls": [{"url": p["url"]} for p in ALL_FB_PAGES],
            "limitPerSource": 8,
            "scrapePageAds-dot-activeStatus": "active",
            "scrapePageAds-dot-period": "last30d",
            "scrapePageAds-dot-sortBy": "most_recent",
        }, wait=120)

        # 整理成精簡格式
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
                "format": snap.get("display_format", ""),
                "platforms": ad.get("publisher_platform", []),
                "start_date": ad.get("start_date_formatted", ""),
                "is_active": ad.get("is_active", False),
                "ad_library_url": ad.get("ad_library_url", ""),
                "own": own_flag,
                "_source": "Meta Ads Library",
            })

        print(f"  -> {len(result)} 則廣告")
        return result
    except Exception as e:
        print(f"  [Meta Ads] 失敗：{e}")
        return []


# ══════════════════════════════════════════════════════
# PART 3 — GSC + Trends
# ══════════════════════════════════════════════════════

def fetch_gsc() -> dict:
    print("[GSC] 關鍵字資料...")
    try:
        tok   = google_token("https://www.googleapis.com/auth/webmasters.readonly")
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=28)).isoformat()
        site_encoded = requests.utils.quote(GSC_SITE, safe="")

        resp = requests.post(
            f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site_encoded}/searchAnalytics/query",
            headers={"Authorization": f"Bearer {tok}"},
            json={"startDate": start, "endDate": today.isoformat(),
                  "dimensions": ["query"], "rowLimit": 20,
                  "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}]},
            timeout=30)
        resp.raise_for_status()
        rows = resp.json().get("rows", [])

        keywords, opportunities = [], []
        for row in rows:
            kw = row["keys"][0]
            clicks      = int(row.get("clicks", 0))
            impressions = int(row.get("impressions", 0))
            ctr         = round(row.get("ctr", 0) * 100, 1)
            position    = round(row.get("position", 0), 1)
            keywords.append({"keyword": kw, "clicks": clicks,
                             "impressions": impressions, "ctr": ctr, "position": position})
            if impressions > 500 and ctr < 3:
                opportunities.append({"keyword": kw, "impressions": impressions,
                                       "ctr": ctr, "position": position})

        print(f"  -> {len(keywords)} 關鍵字，{len(opportunities)} 機會點")
        return {"keywords": keywords, "opportunities": opportunities}
    except Exception as e:
        print(f"  [GSC] 失敗：{e}")
        return {"keywords": [], "opportunities": []}


def fetch_trends() -> list:
    print("[Trends] 市場趨勢...")
    try:
        items = apify_run("apify~google-trends-scraper", {
            "searchTerms": TREND_KEYWORDS,
            "geo": "SG",
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

def claude_call(prompt: str, max_tokens: int = 8192) -> dict:
    """共用 Claude API 呼叫函數"""
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180)
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def analyze_brand_sg(brand: str, reviews: list, ads: list, gsc_text: str, opp_text: str, trends_text: str, own: bool) -> dict:
    """單一品牌深度分析"""
    brand_ads = json.dumps(ads, ensure_ascii=False)
    brand_reviews_text = json.dumps(reviews, ensure_ascii=False)
    own_label = "自家品牌" if own else "競品"

    prompt = f"""你是 ALUXE 珠寶品牌的 SG 市場行銷分析師。
針對【{brand}】（{own_label}）進行深度分析，輸出純 JSON（不含其他文字）：

分析重點：
1. 廣告策略：這個品牌目前在打什麼廣告？用什麼訴求、格式、優惠機制？
2. 顧客評論洞察：顧客最在意什麼？有什麼正面/負面主題？
3. {"ALUXE 可以如何從此品牌借鏡或做出差異化？" if not own else "ALUXE 自身的優勢是什麼？哪些地方可以強化？"}

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
  "strategy_insight": "廣告策略洞察與{"ALUXE強化建議" if own else "ALUXE借鏡方向"}"
}}

評論資料（近14天）：{brand_reviews_text}
廣告資料：{brand_ads}
{"GSC關鍵字：" + gsc_text if gsc_text and own else ""}
{"SEO機會：" + opp_text if opp_text and own else ""}
{"Google Trends SG：" + trends_text if trends_text and own else ""}"""

    result = claude_call(prompt)
    result["brand"] = brand
    return result


def analyze(reviews: list, ads: list, gsc: dict, trends: list) -> dict:
    print("[Claude] 分析中（每品牌獨立分析）...")

    # 整理資料
    from collections import defaultdict
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
    TOTAL_ADS = 40
    BASE_PER_BRAND = 8

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

    gsc_text    = json.dumps(gsc.get("keywords", [])[:10], ensure_ascii=False) if gsc.get("keywords") else ""
    opp_text    = json.dumps(gsc.get("opportunities", [])[:5], ensure_ascii=False) if gsc.get("opportunities") else ""
    trends_text = json.dumps(trends[:5], ensure_ascii=False) if trends else ""

    # 所有品牌清單
    all_brands = ["ALUXE", "Jannpaul", "Michael Trio", "Lee Hwa", "Love & Co"]
    brand_results = {}

    for brand in all_brands:
        print(f"  [Claude] 分析 {brand}...")
        b_reviews = brand_reviews.get(brand, [])[:10]
        b_ads = ads_by_brand.get(brand, [])
        own = "ALUXE" in brand
        try:
            result = analyze_brand_sg(brand, b_reviews, b_ads, gsc_text, opp_text, trends_text, own)
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

    # 整體摘要 + actionable_top3
    print("  [Claude] 產出整體摘要...")
    summary_data = {b: {k: v for k, v in r.items() if k in ["strategy_insight", "top_themes", "cta_focus", "key_offers", "alert"]}
                    for b, r in brand_results.items()}
    summary_prompt = f"""你是 ALUXE 珠寶品牌的 SG 市場行銷分析師。
根據以下各品牌分析結果，輸出純 JSON（不含其他文字）：

請避免建議：員工個人IP、員工故事、社群人格化

{{
  "summary": "2-3句整體觀察，聚焦競品策略動態與 ALUXE 可學習的方向",
  "competitor_alerts": [{{"brand":"","issue":"","severity":1-5,"opportunity":""}}],
  "hot_topics": [{{"topic":"","volume":"high/medium/low","actionable":true,"suggestion":"具體的廣告切角或部落格主題建議"}}],
  "gsc_insights": {{
    "top_keywords": [{{"keyword":"","clicks":0,"impressions":0,"ctr":0.0,"position":0.0}}],
    "opportunities": [{{"keyword":"","impressions":0,"ctr":0.0,"suggestion":"可寫成部落格文章或廣告素材的方向"}}]
  }},
  "market_trends": [{{"keyword":"","trend":"rising/stable/falling","insight":"對 ALUXE 廣告或內容策略的意義"}}],
  "actionable_top3": [
    "🎯 本週可學習的競品廣告動作：（具體說明哪個品牌做了什麼、ALUXE 可以怎麼借鏡）",
    "📝 內容行銷機會：（具體的部落格主題或廣告素材切角）",
    "🔍 SEO／關鍵字機會：（從 Trends 或 GSC 找到的可優化方向）"
  ]
}}

各品牌分析摘要：{json.dumps(summary_data, ensure_ascii=False)}
GSC關鍵字：{gsc_text}
SEO機會：{opp_text}
Google Trends SG：{trends_text}"""

    summary_result = claude_call(summary_prompt)

    # 合併結果
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
        "gsc_insights": summary_result.get("gsc_insights", {"top_keywords": [], "opportunities": []}),
        "market_trends": summary_result.get("market_trends", []),
        "actionable_top3": summary_result.get("actionable_top3", []),
        "generated_at": datetime.datetime.utcnow().isoformat(),
    }
    print("[Claude] SG 完成")
    return result


# ══════════════════════════════════════════════════════
# PART 5 — Google Sheets
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

def write_sheets(report: dict):
    print("[Sheets] 寫入中...")
    tok  = sheets_token()
    date = report["generated_at"][:10]
    own  = [v["sentiment_score"] for k,v in report["brands"].items()
            if "ALUXE" in k and "JOY" not in k and "acredo" not in k]
    avg  = round(sum(own)/len(own), 2) if own else 0

    # Dashboard
    rows = [["ALUXE SG 輿情監控 v4","","","","","","",""],
            ["最後更新", date, "自家品牌平均分", avg,"","","",""],
            ["","","","","","","",""],
            ["品牌","情感分","正面%","負面%","中性%","評論數","來源","預警"]]
    for n,d in report["brands"].items():
        rows.append([n, d.get("sentiment_score",""), d.get("positive_pct",""),
                     d.get("negative_pct",""), d.get("neutral_pct",""),
                     d.get("review_count",""), ", ".join(d.get("sources",[])),
                     d.get("alert") or "—"])
    sheets_update(tok, "Dashboard_SG", "A1", rows)

    # Weekly History
    for n,d in report["brands"].items():
        sheets_append(tok, "Weekly History_SG", [[
            date, n, d.get("sentiment_score",""), d.get("positive_pct",""),
            d.get("negative_pct",""), d.get("neutral_pct",""), d.get("review_count",""),
            ", ".join(d.get("sources",[])), ", ".join(d.get("top_themes",[])),
            d.get("alert") or ""]])

    # Competitor Alerts
    for a in report.get("competitor_alerts",[]):
        sheets_append(tok, "Competitor Alerts_SG", [[
            date, a.get("brand",""), a.get("severity",""),
            a.get("issue",""), a.get("opportunity","")]])

    # Hot Topics
    for t in report.get("hot_topics",[]):
        sheets_append(tok, "Hot Topics_SG", [[
            date, t.get("topic",""), t.get("volume",""),
            "是" if t.get("actionable") else "否", t.get("suggestion","")]])

    # Action Log
    for i,a in enumerate(report.get("actionable_top3",[]), 1):
        sheets_append(tok, "Action Log_SG", [[date, f"優先 {i}", a, "待執行"]])

    # GSC Keywords
    for kw in report.get("gsc_insights",{}).get("top_keywords",[]):
        sheets_append(tok, "GSC Keywords_SG", [[
            date, kw.get("keyword",""), kw.get("clicks",""),
            kw.get("impressions",""), kw.get("ctr",""), kw.get("position","")]])

    # Competitor Ads（新增工作表）
    for brand, ad_data in report.get("competitor_ads",{}).items():
        sheets_append(tok, "Competitor Ads_SG", [[
            date, brand,
            ad_data.get("ad_count",""),
            ", ".join(ad_data.get("main_themes",[])),
            ad_data.get("cta_focus",""),
            ", ".join(ad_data.get("platforms",[])),
            ", ".join(ad_data.get("key_offers",[])),
            ad_data.get("strategy_insight",""),
        ]])

    print("[Sheets] 完成（7 個工作表）")


# ══════════════════════════════════════════════════════
# PART 6 — HTML 儀表板（靜態 index.html + config.json）
# ══════════════════════════════════════════════════════

def generate_html(report: dict):
    import shutil
    # 複製靜態 index.html 到 docs/（如果存在）
    static_html = Path("docs/index.html")
    # index.html 已直接放在 docs/，不需要複製
    # 只需更新 config.json 確保地區清單正確
    config = {
        "markets": [
            {"id": "sg", "label": "🇸🇬 Singapore", "data_file": "sg_latest.json", "active": True},
            {"id": "hk", "label": "🇭🇰 Hong Kong",  "data_file": "hk_latest.json", "active": True},
        ]
    }
    (OUTPUT_DIR / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))
    print("[HTML] config.json 更新完成")
# ══════════════════════════════════════════════════════
# PART 7 — Telegram
# ══════════════════════════════════════════════════════

def send_telegram(report: dict):
    date = report.get("generated_at","")[:10]
    own  = [v["sentiment_score"] for k,v in report.get("brands",{}).items()
            if "ALUXE" in k and "JOY" not in k]
    avg  = round(sum(own)/len(own),2) if own else 0
    icon = "📈" if avg>=0.7 else "📊" if avg>=0.5 else "📉"

    alerts = "\n".join(
        f"{'🔴' if a.get('severity',1)>=4 else '🟡'} {a['brand']} — {a['issue']}"
        for a in report.get("competitor_alerts",[])) or "本週無預警"

    actions = "\n".join(f"{i+1}. {a}" for i,a in enumerate(report.get("actionable_top3",[])))

    # 廣告摘要
    ads_summary = ""
    for brand, ad_data in report.get("competitor_ads",{}).items():
        count = ad_data.get("ad_count", 0)
        focus = ad_data.get("cta_focus","")
        ads_summary += f"\n· {brand}：{count} 則廣告，主打「{focus}」"

    gsc = report.get("gsc_insights",{})
    gsc_line = ""
    if gsc.get("top_keywords"):
        top3 = ", ".join(k["keyword"] for k in gsc["top_keywords"][:3])
        gsc_line = f"\n\n🔍 GSC 前3關鍵字：{top3}"
    if gsc.get("opportunities"):
        gsc_line += f"\n⚡ 機會點：{gsc['opportunities'][0]['keyword']}"

    msg = (f"ALUXE SG 輿情週報 · {date}\n\n"
           f"{icon} 自家品牌平均分：{avg}"
           f"{gsc_line}\n\n"
           f"競品負評預警\n{alerts}\n\n"
           f"競品廣告動態{ads_summary}\n\n"
           f"本週優先行動\n{actions}\n\n"
           f"儀表板：{PAGES_URL}\n"
           f"數據：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=30,
    ).raise_for_status()
    print("[Telegram] 完成")


# ══════════════════════════════════════════════════════
# PART 8 — 存檔
# ══════════════════════════════════════════════════════

def save_json(report: dict):
    hf = OUTPUT_DIR / "sg_history.json"
    history = json.loads(hf.read_text()) if hf.exists() else []
    history.insert(0, report)
    hf.write_text(json.dumps(history[:26], ensure_ascii=False, indent=2))
    (OUTPUT_DIR / "sg_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("[JSON] 完成")


# ══════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════

def main():
    print(f"\n{'='*52}")
    print(f"  ALUXE SG Sentiment v4  —  {datetime.date.today()}")
    print(f"{'='*52}\n")

    reviews = fetch_reviews()
    ig      = fetch_instagram()
    ads     = fetch_meta_ads()
    gsc     = fetch_gsc()
    trends  = fetch_trends()

    all_reviews = reviews + ig
    report  = analyze(all_reviews, ads, gsc, trends)

    save_json(report)
    generate_html(report)
    write_sheets(report)
    send_telegram(report)

    print(f"\n✅ 完成")
    print(f"   網頁：{PAGES_URL}")
    print(f"   試算表：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")


if __name__ == "__main__":
    main()
