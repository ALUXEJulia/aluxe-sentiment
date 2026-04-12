"""
ALUXE SG Sentiment Analysis Pipeline — v4
新增：Meta 廣告競品監控、Instagram 爬蟲修復、評論時間範圍控制、費用優化
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

# Meta 廣告資料庫 — 競品粉絲頁
COMPETITOR_FB_PAGES = [
    {"name": "Jannpaul",     "url": "https://www.facebook.com/JANNPAULDiamonds"},
    {"name": "Michael Trio", "url": "https://www.facebook.com/michaeltriojewels"},
    {"name": "Love & Co",    "url": "https://www.facebook.com/L0veandC0"},
    {"name": "Lee Hwa",      "url": "https://www.facebook.com/LeeHwaJewellery"},
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
    print("[Apify] Google Maps 評論（最新 15 則）...")
    maps = apify_run("compass~google-maps-reviews-scraper", {
        "searchStringsArray": ALL_BRANDS,
        "maxReviews": 15,           # 從 30 降到 15，省費用
        "language": "en",
        "reviewsSort": "newest",    # 只抓最新
    })
    # 過濾只保留近 90 天的評論
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=90)
    filtered = []
    for item in maps:
        item["_source"] = "Google Maps"
        # 嘗試過濾時間，沒有時間欄位就保留
        pub_date = item.get("publishedAtDate") or item.get("date") or ""
        if pub_date:
            try:
                dt = datetime.datetime.fromisoformat(pub_date[:19])
                if dt >= cutoff:
                    filtered.append(item)
            except Exception:
                filtered.append(item)
        else:
            filtered.append(item)
    print(f"  -> {len(filtered)} 筆（近 90 天）")
    return filtered


def fetch_instagram() -> list:
    print("[Apify] Instagram 最新貼文留言...")
    try:
        # Step 1: 先抓各帳號最新貼文 URL
        posts = apify_run("apify~instagram-scraper", {
            "directUrls": [f"https://www.instagram.com/{h}/" for h in IG_HANDLES],
            "resultsType": "posts",
            "resultsLimit": 3,      # 每個帳號抓最新 3 則貼文
        }, wait=90)

        post_urls = [p.get("url") or p.get("displayUrl") for p in posts if p.get("url") or p.get("displayUrl")]
        post_urls = [u for u in post_urls if u and "instagram.com/p/" in u]

        if not post_urls:
            print("  -> 無法取得貼文 URL，略過")
            return []

        # Step 2: 抓留言
        comments = apify_run("apify/instagram-comment-scraper", {
            "directUrls": post_urls[:10],  # 最多 10 則貼文
            "resultsLimit": 20,
        }, wait=90)

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
    print("[Meta Ads] 抓取競品廣告...")
    try:
        ads = apify_run("curious_coder~facebook-ads-library-scraper", {
            "urls": [{"url": p["url"]} for p in COMPETITOR_FB_PAGES],
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
            result.append({
                "brand": ad.get("page_name", ""),
                "title": snap.get("title", ""),
                "body": body.get("text", "") if isinstance(body, dict) else str(body),
                "cta": snap.get("cta_text", ""),
                "format": snap.get("display_format", ""),
                "platforms": ad.get("publisher_platform", []),
                "start_date": ad.get("start_date_formatted", ""),
                "is_active": ad.get("is_active", False),
                "ad_library_url": ad.get("ad_library_url", ""),
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

def analyze(reviews: list, ads: list, gsc: dict, trends: list) -> dict:
    print("[Claude] 分析中...")

    # 廣告摘要
    ads_by_brand = {}
    for ad in ads:
        brand = ad.get("brand", "Unknown")
        if brand not in ads_by_brand:
            ads_by_brand[brand] = []
        ads_by_brand[brand].append({
            "title": ad.get("title", ""),
            "body": (ad.get("body") or "")[:200],
            "cta": ad.get("cta", ""),
            "platforms": ad.get("platforms", []),
            "start_date": ad.get("start_date", ""),
        })

    ads_summary = json.dumps(ads_by_brand, ensure_ascii=False)
    gsc_text    = json.dumps(gsc.get("keywords", [])[:10], ensure_ascii=False) if gsc.get("keywords") else ""
    opp_text    = json.dumps(gsc.get("opportunities", [])[:5], ensure_ascii=False) if gsc.get("opportunities") else ""
    trends_text = json.dumps(trends[:5], ensure_ascii=False) if trends else ""

    prompt = f"""你是 ALUXE 珠寶品牌的 SG 市場行銷分析師。
分析以下資料，輸出純 JSON（不含其他文字）：

{{
  "summary": "2-3句整體觀察，整合評論、廣告、GSC洞察",
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
      "main_themes": ["廣告主題1","廣告主題2"],
      "cta_focus": "主要CTA方向",
      "platforms": ["FB","IG"],
      "key_offers": ["優惠1","優惠2"],
      "strategy_insight": "廣告策略洞察"
    }}
  }},
  "hot_topics": [{{"topic":"","volume":"high/medium/low","actionable":true,"suggestion":""}}],
  "gsc_insights": {{
    "top_keywords": [{{"keyword":"","clicks":0,"impressions":0,"ctr":0.0,"position":0.0}}],
    "opportunities": [{{"keyword":"","impressions":0,"ctr":0.0,"suggestion":"建議"}}]
  }},
  "market_trends": [{{"keyword":"","trend":"rising/stable/falling","insight":"市場意義"}}],
  "actionable_top3": ["行動1","行動2","行動3"]
}}

評論資料（近 90 天）：{json.dumps(reviews[:60], ensure_ascii=False)}

競品 Meta 廣告資料：{ads_summary}

GSC 關鍵字：{gsc_text}
SEO 機會點：{opp_text}
Google Trends SG：{trends_text}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": 6000,
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
    print("[Claude] 完成")
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
    sheets_update(tok, "Dashboard", "A1", rows)

    # Weekly History
    for n,d in report["brands"].items():
        sheets_append(tok, "Weekly History", [[
            date, n, d.get("sentiment_score",""), d.get("positive_pct",""),
            d.get("negative_pct",""), d.get("neutral_pct",""), d.get("review_count",""),
            ", ".join(d.get("sources",[])), ", ".join(d.get("top_themes",[])),
            d.get("alert") or ""]])

    # Competitor Alerts
    for a in report.get("competitor_alerts",[]):
        sheets_append(tok, "Competitor Alerts", [[
            date, a.get("brand",""), a.get("severity",""),
            a.get("issue",""), a.get("opportunity","")]])

    # Hot Topics
    for t in report.get("hot_topics",[]):
        sheets_append(tok, "Hot Topics", [[
            date, t.get("topic",""), t.get("volume",""),
            "是" if t.get("actionable") else "否", t.get("suggestion","")]])

    # Action Log
    for i,a in enumerate(report.get("actionable_top3",[]), 1):
        sheets_append(tok, "Action Log", [[date, f"優先 {i}", a, "待執行"]])

    # GSC Keywords
    for kw in report.get("gsc_insights",{}).get("top_keywords",[]):
        sheets_append(tok, "GSC Keywords", [[
            date, kw.get("keyword",""), kw.get("clicks",""),
            kw.get("impressions",""), kw.get("ctr",""), kw.get("position","")]])

    # Competitor Ads（新增工作表）
    for brand, ad_data in report.get("competitor_ads",{}).items():
        sheets_append(tok, "Competitor Ads", [[
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
# PART 6 — HTML 儀表板
# ══════════════════════════════════════════════════════

def generate_html(report: dict):
    date = report.get("generated_at","")[:10]
    today = datetime.date.today()
    date_90d_start = (today - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    date_28d_start = (today - datetime.timedelta(days=28)).strftime("%Y-%m-%d")
    date_30d_start = (today - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    def ts(label): return f'<div class="ts">資料區間：{label}</div>'

    def brand_card(name, d):
        sc  = d.get("sentiment_score",0)
        col = "#1D9E75" if sc>=0.7 else "#BA7517" if sc>=0.5 else "#E24B4A"
        own = "ALUXE" in name and "JOY" not in name and "acredo" not in name
        themes  = "".join(f'<span class="tag">{t}</span>' for t in d.get("top_themes",[]))
        sources = "".join(f'<span class="src-tag">{s}</span>' for s in d.get("sources",[]))
        alert   = f'<div class="alert-box">{d["alert"]}</div>' if d.get("alert") else ""
        pos_q   = f'<div class="quote pos-q">"{d["sample_positive"]}"</div>' if d.get("sample_positive") else ""
        neg_q   = f'<div class="quote neg-q">"{d["sample_negative"]}"</div>' if d.get("sample_negative") else ""
        own_badge = '<span class="own-badge">自家品牌</span>' if own else ''
        return f"""<div class="bc {'own' if own else 'comp'}">
          <div class="bh"><span class="bn">{name}</span>
            <span style="font-size:20px;font-weight:500;color:{col}">{sc:.2f}</span></div>
          {own_badge}
          <div class="sources">{sources}</div>
          <div class="bbar">
            <div style="width:{d.get('positive_pct',0)}%;background:#1D9E75"></div>
            <div style="width:{d.get('neutral_pct',0)}%;background:#D3D1C7"></div>
            <div style="width:{d.get('negative_pct',0)}%;background:#E24B4A"></div>
          </div>
          <div class="blbl">
            <span style="color:#1D9E75">正 {d.get('positive_pct',0)}%</span>
            <span style="color:#888">中 {d.get('neutral_pct',0)}%</span>
            <span style="color:#E24B4A">負 {d.get('negative_pct',0)}%</span>
            <span style="color:#aaa;margin-left:auto">{d.get('review_count',0)} 則</span>
          </div>
          <div class="themes">{themes}</div>
          {alert}{pos_q}{neg_q}</div>"""

    brands_html = "".join(brand_card(n,d) for n,d in report.get("brands",{}).items())

    alerts_html = "".join(f"""<div class="ai">
        <div style="font-size:13px;font-weight:500;color:{'#E24B4A' if a.get('severity',1)>=4 else '#BA7517'}">
          {a['brand']} <span style="font-size:11px;font-weight:400;margin-left:8px">嚴重度 {a.get('severity')}/5</span></div>
        <div style="font-size:13px;color:#3D3A32;margin:4px 0">{a['issue']}</div>
        <div style="font-size:12px;color:#1D9E75">機會：{a['opportunity']}</div></div>"""
        for a in report.get("competitor_alerts",[])) or '<p style="color:#7A7669;font-size:13px">本週無預警</p>'

    # 競品廣告橫排卡片
    ads_cards_html = ""
    for brand, ad_data in report.get("competitor_ads",{}).items():
        themes = "".join(f'<span class="tag">{t}</span>' for t in ad_data.get("main_themes",[]))
        offers = "".join(f'<span class="tag" style="background:#E1F5EE;color:#085041">{o}</span>'
                        for o in ad_data.get("key_offers",[]))
        plats  = " · ".join(ad_data.get("platforms",[]))
        ads_cards_html += f"""<div class="ad-card">
          <div class="ad-brand">{brand}</div>
          <div class="ad-count-row"><span class="ad-count">{ad_data.get('ad_count',0)}</span><span class="ad-count-lbl">則現役廣告</span></div>
          <div style="font-size:10px;color:#7A5F10;margin-bottom:6px">{plats}</div>
          <div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:5px">{themes}</div>
          <div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:6px">{offers}</div>
          <div class="ad-cta">主打 CTA：<strong>{ad_data.get('cta_focus','')}</strong></div>
          <div class="ad-insight">{ad_data.get('strategy_insight','')}</div>
        </div>"""

    topics_html = "".join(f"""<div class="ti">
        <div style="font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px;margin-bottom:4px">
          {t['topic']}
          {'<span class="act-badge">可介入</span>' if t.get('actionable') else ''}
          <span style="font-size:11px;color:{'#1D9E75' if t.get('volume')=='high' else '#BA7517'}">{t.get('volume','').upper()}</span>
        </div>
        <div style="font-size:12px;color:#7A7669">{t.get('suggestion','')}</div></div>"""
        for t in report.get("hot_topics",[]))

    gsc = report.get("gsc_insights",{})
    gsc_kw_html = ""
    if gsc.get("top_keywords"):
        rows = "".join(f"""<tr>
            <td style="padding:6px 8px;font-size:12px">{k['keyword']}</td>
            <td style="padding:6px 8px;font-size:12px;text-align:right">{k['clicks']:,}</td>
            <td style="padding:6px 8px;font-size:12px;text-align:right">{k['impressions']:,}</td>
            <td style="padding:6px 8px;font-size:12px;text-align:right">{k['ctr']}%</td>
            <td style="padding:6px 8px;font-size:12px;text-align:right">#{k['position']}</td>
            </tr>""" for k in gsc["top_keywords"][:10])
        gsc_kw_html = f"""<table style="width:100%;border-collapse:collapse">
            <thead><tr style="border-bottom:0.5px solid var(--border)">
            <th style="padding:6px 8px;text-align:left;font-weight:500;color:#7A7669;font-size:12px">關鍵字</th>
            <th style="padding:6px 8px;text-align:right;font-weight:500;color:#7A7669;font-size:12px">點擊</th>
            <th style="padding:6px 8px;text-align:right;font-weight:500;color:#7A7669;font-size:12px">曝光</th>
            <th style="padding:6px 8px;text-align:right;font-weight:500;color:#7A7669;font-size:12px">CTR</th>
            <th style="padding:6px 8px;text-align:right;font-weight:500;color:#7A7669;font-size:12px">排名</th>
            </tr></thead><tbody>{rows}</tbody></table>
            {ts(f"{date_28d_start} 至 {date}")}"""

    opp_html = ""
    if gsc.get("opportunities"):
        opp_html = "".join(f"""<div class="ti">
            <div style="font-size:13px;font-weight:500;margin-bottom:4px">{o['keyword']}
              <span style="font-size:11px;color:#BA7517;margin-left:8px">曝光 {o['impressions']:,} · CTR {o['ctr']}%</span>
            </div>
            <div style="font-size:12px;color:#7A7669">{o.get('suggestion','')}</div></div>"""
            for o in gsc["opportunities"])
        opp_html += ts(f"{date_28d_start} 至 {date}")

    trends_html = ""
    if report.get("market_trends"):
        trends_html = "".join(f"""<div class="ti">
            <div style="font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px;margin-bottom:4px">
              {t['keyword']}
              <span style="font-size:11px;color:{'#1D9E75' if t.get('trend')=='rising' else '#E24B4A' if t.get('trend')=='falling' else '#888'}">
                {'↑ 上升' if t.get('trend')=='rising' else '↓ 下降' if t.get('trend')=='falling' else '→ 穩定'}
              </span>
            </div>
            <div style="font-size:12px;color:#7A7669">{t.get('insight','')}</div></div>"""
            for t in report["market_trends"])
        trends_html += ts(f"{date_30d_start} 至 {date}（過去 30 天）")

    actions_html = "".join(
        f'<div class="action-item"><span class="anum">{i+1}</span><span>{a}</span></div>'
        for i,a in enumerate(report.get("actionable_top3",[])))

    html = f"""<!DOCTYPE html><html lang="zh-TW"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ALUXE SG Sentiment</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--gold:#B8973E;--gold-l:#F5EDD8;--dark:#1A1814;--bg:#FAF9F6;--card:#fff;--border:rgba(0,0,0,.08)}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--dark)}}
header{{background:var(--dark);padding:1.5rem 2rem;display:flex;justify-content:space-between;align-items:center}}
h1{{font-family:'DM Serif Display',serif;color:var(--gold);font-size:20px;font-weight:400;letter-spacing:.04em}}
.wrap{{max-width:1060px;margin:0 auto;padding:2rem 1.5rem}}
.summary{{background:var(--gold-l);border-left:3px solid var(--gold);border-radius:0 8px 8px 0;padding:1rem 1.25rem;font-size:14px;line-height:1.7;color:#3D3A32;margin-bottom:2rem}}
.sec{{margin-bottom:2.5rem}}
.sec-label{{font-size:11px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:#7A7669;margin-bottom:.75rem}}
.ts{{font-size:10px;color:#aaa;margin-top:.75rem;padding-top:.5rem;border-top:0.5px solid var(--border)}}

.ads-banner{{background:var(--card);border:1px solid var(--gold);border-radius:12px;padding:1.25rem;margin-bottom:2rem}}
.ads-banner-top{{display:flex;align-items:center;gap:10px;margin-bottom:1rem;padding-bottom:.75rem;border-bottom:0.5px solid #E8D9A8}}
.ads-badge{{background:var(--dark);color:var(--gold);font-size:10px;font-weight:500;padding:3px 12px;border-radius:20px;letter-spacing:.08em}}
.ads-subtitle{{font-size:11px;color:#7A7669}}
.ads-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}
.ad-card{{background:#FAF9F6;border:0.5px solid #E8D9A8;border-radius:10px;padding:.875rem}}
.ad-brand{{font-size:11px;font-weight:500;color:#7A5F10;margin-bottom:2px}}
.ad-count-row{{display:flex;align-items:baseline;gap:4px;margin-bottom:4px}}
.ad-count{{font-size:22px;font-weight:500;color:var(--dark)}}
.ad-count-lbl{{font-size:10px;color:#7A7669}}
.ad-cta{{font-size:10px;color:#7A7669;border-top:0.5px solid #E8D9A8;padding-top:5px;margin-top:5px}}
.ad-cta strong{{color:#7A5F10;font-weight:500}}
.ad-insight{{font-size:10px;color:#555;margin-top:4px;line-height:1.4;border-left:2px solid var(--gold);padding-left:6px}}
.ads-ts{{font-size:10px;color:#aaa;margin-top:.875rem;padding-top:.625rem;border-top:0.5px solid #E8D9A8}}

.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}}
.bc{{background:var(--card);border:.5px solid var(--border);border-radius:12px;padding:1.25rem}}
.bc.own{{border-color:var(--gold);border-width:1.5px}}
.own-badge{{display:inline-block;font-size:9px;background:var(--gold-l);color:#7A5F10;padding:1px 8px;border-radius:10px;margin-bottom:5px}}
.bh{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}}
.bn{{font-size:13px;font-weight:500}}
.sources{{display:flex;gap:4px;margin-bottom:6px}}
.src-tag{{background:#E6F1FB;color:#185FA5;font-size:10px;padding:1px 7px;border-radius:20px}}
.bbar{{display:flex;height:5px;border-radius:3px;overflow:hidden;margin-bottom:5px;gap:1px}}
.blbl{{display:flex;gap:8px;font-size:10px;margin-bottom:7px;align-items:center}}
.themes{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}}
.tag{{background:#F1EFE8;color:#3D3A32;font-size:10px;padding:2px 8px;border-radius:20px}}
.alert-box{{background:#FFF0F0;color:#A32D2D;font-size:11px;padding:5px 8px;border-radius:6px;border-left:2px solid #E24B4A;margin-bottom:4px}}
.quote{{font-size:10px;font-style:italic;margin-top:4px;padding-top:4px;border-top:0.5px solid var(--border);line-height:1.4}}
.pos-q{{color:#0F6E56}}
.neg-q{{color:#A32D2D}}
.ai,.ti{{background:var(--card);border:.5px solid var(--border);border-radius:10px;padding:1rem 1.25rem;margin-bottom:8px}}
.act-badge{{background:#E1F5EE;color:#085041;font-size:10px;padding:1px 8px;border-radius:20px;font-weight:400}}
.action-item{{display:flex;gap:12px;align-items:flex-start;padding:.875rem 1.25rem;background:var(--card);border:.5px solid var(--border);border-radius:10px;margin-bottom:8px;font-size:13px;line-height:1.5}}
.anum{{width:22px;height:22px;border-radius:50%;background:var(--dark);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0;margin-top:1px}}
.ext-link{{display:inline-block;margin-top:.75rem;font-size:12px;color:var(--gold);text-decoration:none;border:.5px solid var(--gold);padding:4px 14px;border-radius:20px;margin-right:8px}}
table tr:hover td{{background:#F9F8F5}}
footer{{text-align:center;font-size:11px;color:#7A7669;padding:2rem;border-top:.5px solid var(--border);margin-top:1rem}}
</style></head><body>
<header><h1>ALUXE · SG Sentiment Monitor v4</h1>
<span style="color:#7A7669;font-size:12px">報告日期：{date}</span></header>
<div class="wrap">

  <div class="ads-banner">
    <div class="ads-banner-top">
      <span class="ads-badge">競品廣告監控</span>
      <span class="ads-subtitle">本月現役廣告 · Meta 廣告資料庫 · 自動更新</span>
    </div>
    <div class="ads-grid">
      {ads_cards_html if ads_cards_html else '<p style="color:#7A7669;font-size:13px">無廣告資料</p>'}
    </div>
    <div class="ads-ts">資料區間：{date_30d_start} 至 {date}（過去 30 天，僅限現役廣告）· 來源：Meta 廣告資料庫</div>
  </div>

  <div class="summary">{report.get('summary','')}</div>

  <div class="sec">
    <div class="sec-label">品牌情感分析 · 自家品牌 &amp; 競品</div>
    <div class="grid">{brands_html}</div>
    {ts(f"{date_90d_start} 至 {date}（近 90 天）· 來源：Google Maps 評論")}
  </div>

  <div class="sec">
    <div class="sec-label">競品負評預警</div>
    {alerts_html}
    {ts(f"{date_90d_start} 至 {date}（近 90 天）")}
  </div>

  <div class="sec">
    <div class="sec-label">熱門議題 · 可操作清單</div>
    {topics_html}
    {ts(f"{date_90d_start} 至 {date}（近 90 天 Google Maps 評論綜合分析）")}
  </div>

  <div class="sec">
    <div class="sec-label">Google Search Console · 前10大關鍵字</div>
    {gsc_kw_html if gsc_kw_html else f'<p style="color:#7A7669;font-size:13px">GSC 資料待串接</p>{ts("串接完成後將顯示過去 28 天資料")}'}
  </div>

  {'<div class="sec"><div class="sec-label">SEO 機會點 · 曝光高但點擊率低</div>' + opp_html + '</div>' if opp_html else ''}

  {'<div class="sec"><div class="sec-label">市場搜尋趨勢 · Google Trends SG</div>' + trends_html + '</div>' if trends_html else ''}

  <div class="sec">
    <div class="sec-label">本週優先行動 Top 3</div>
    {actions_html}
    {ts(f"根據 {date} 週報綜合分析產出")}
    <a class="ext-link" href="https://docs.google.com/spreadsheets/d/{SHEETS_ID}" target="_blank">歷史數據 Google Sheets</a>
  </div>

</div>
<footer>ALUXE Marketing Intelligence · Claude + Apify + Meta Ads · {date}</footer>
</body></html>"""

    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    print("[HTML] 完成")


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
    hf = OUTPUT_DIR / "history.json"
    history = json.loads(hf.read_text()) if hf.exists() else []
    history.insert(0, report)
    hf.write_text(json.dumps(history[:26], ensure_ascii=False, indent=2))
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
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
