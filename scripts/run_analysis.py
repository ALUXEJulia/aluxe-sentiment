"""
ALUXE SG Sentiment Analysis Pipeline — v2
輸出：GitHub Pages 儀表板 + Google Sheets 歷史記錄 + Telegram 週報

環境變數:
  APIFY_TOKEN
  ANTHROPIC_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  GOOGLE_SHEETS_ID          # Sheet URL 中間那段 ID
  GOOGLE_SERVICE_ACCOUNT    # Service Account 整個 JSON 字串
  PAGES_URL          # 你的 GitHub Pages 網址（選填）
"""

import os, json, datetime, base64, requests
from pathlib import Path

APIFY_TOKEN       = os.environ["APIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
SHEETS_ID         = os.environ["GOOGLE_SHEETS_ID"]
SERVICE_ACCOUNT   = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
PAGES_URL  = os.environ.get("PAGES_URL", "https://[你的帳號].github.io/aluxe-sentiment/")

BRANDS_OWN        = ["ALUXE Singapore", "JOY COLORi Singapore", "acredo Singapore"]
BRANDS_COMP       = ["Jannpaul Singapore", "Michael Trio Singapore",
                     "Love and Co Singapore", "Lee Hwa Jewellery Singapore"]
ALL_BRANDS        = BRANDS_OWN + BRANDS_COMP

OUTPUT_DIR = Path("docs")
OUTPUT_DIR.mkdir(exist_ok=True)


# ── 1. Apify ──────────────────────────────────────────

def apify_run(actor_id: str, payload: dict, wait: int = 120) -> list:
    url = (f"https://api.apify.com/v2/acts/{actor_id}/runs"
           f"?token={APIFY_TOKEN}&waitForFinish={wait}")
    r = requests.post(url, json=payload, timeout=wait + 60)
    r.raise_for_status()
    ds = r.json()["data"]["defaultDatasetId"]
    items = requests.get(
        f"https://api.apify.com/v2/datasets/{ds}/items?token={APIFY_TOKEN}&clean=true",
        timeout=60,
    ).json()
    return items if isinstance(items, list) else []


def fetch_data() -> list:
    print("[Apify] Google Maps 評論...")
    maps = apify_run("compass~google-maps-reviews-scraper", {
        "searchStringsArray": ALL_BRANDS,
        "maxReviews": 30,
        "language": "en",
        "reviewsSort": "newest",
    })
    print(f"  → {len(maps)} 筆")

    print("[Apify] Instagram 留言...")
    ig_handles = [
    "aluxe_sg",
    "jannpaul",
    "michaeltrio",
    "loveandcoofficial",
    "leehwajewellery",
    ]
    ig = apify_run("apify~instagram-comment-scraper", {
        "directUrls": [f"https://www.instagram.com/{h}/" for h in ig_handles],
        "resultsLimit": 15,
    })
    print(f"  → {len(ig)} 筆")
    return maps + ig


# ── 2. Claude 分析 ────────────────────────────────────

def analyze(data: list) -> dict:
    print("[Claude] 分析中...")
    prompt = f"""你是 ALUXE 珠寶品牌的 SG 市場行銷分析師。
分析以下評論資料，輸出純 JSON（不含其他文字）：

{{
  "summary": "2-3 句整體觀察",
  "brands": {{
    "品牌名": {{
      "sentiment_score": 0.0-1.0,
      "positive_pct": 整數,
      "negative_pct": 整數,
      "neutral_pct": 整數,
      "review_count": 整數,
      "top_themes": ["主題1","主題2","主題3"],
      "alert": null或"預警描述",
      "sample_positive": "留言原文或null",
      "sample_negative": "留言原文或null"
    }}
  }},
  "competitor_alerts": [{{"brand":"","issue":"","severity":1-5,"opportunity":""}}],
  "hot_topics": [{{"topic":"","volume":"high/medium/low","actionable":true,"suggestion":""}}],
  "actionable_top3": ["行動1","行動2","行動3"]
}}

資料：{json.dumps(data[:80], ensure_ascii=False)}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-4-5", "max_tokens": 4096,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
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


# ── 3. Google Sheets ──────────────────────────────────

def sheets_token() -> str:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    import time

    def b64(s): return base64.urlsafe_b64encode(s).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    now = int(time.time())
    claim = b64(json.dumps({
        "iss": SERVICE_ACCOUNT["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600, "iat": now,
    }).encode())

    key = serialization.load_pem_private_key(
        SERVICE_ACCOUNT["private_key"].encode(), password=None,
        backend=default_backend())
    sig = b64(key.sign(f"{header}.{claim}".encode(), padding.PKCS1v15(), hashes.SHA256()))
    jwt = f"{header}.{claim}.{sig}"

    resp = requests.post("https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
              "assertion": jwt}, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def sheets_append(tok: str, sheet: str, rows: list):
    requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_ID}"
        f"/values/{sheet}!A1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        headers={"Authorization": f"Bearer {tok}"},
        json={"values": rows}, timeout=30,
    ).raise_for_status()


def sheets_update(tok: str, sheet: str, rng: str, rows: list):
    requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_ID}"
        f"/values/{sheet}!{rng}?valueInputOption=USER_ENTERED",
        headers={"Authorization": f"Bearer {tok}"},
        json={"values": rows}, timeout=30,
    ).raise_for_status()


def write_sheets(report: dict):
    print("[Sheets] 寫入中...")
    tok  = sheets_token()
    date = report["generated_at"][:10]

    own_scores = [v["sentiment_score"] for k, v in report["brands"].items()
                  if any(b in k for b in ["ALUXE","JOY COLORi","acredo"])]
    avg = round(sum(own_scores)/len(own_scores), 2) if own_scores else 0

    # Dashboard（覆寫）
    rows = [
        ["ALUXE SG 輿情監控", "", "", "", "", "", ""],
        ["最後更新", date, "自家品牌平均分", avg, "", "", ""],
        ["", "", "", "", "", "", ""],
        ["品牌", "情感分", "正面%", "負面%", "中性%", "評論數", "預警"],
    ]
    for n, d in report["brands"].items():
        rows.append([n, d.get("sentiment_score",""), d.get("positive_pct",""),
                     d.get("negative_pct",""), d.get("neutral_pct",""),
                     d.get("review_count",""), d.get("alert") or "—"])
    sheets_update(tok, "Dashboard", "A1", rows)

    # Weekly History（追加）
    for n, d in report["brands"].items():
        sheets_append(tok, "Weekly History", [[
            date, n,
            d.get("sentiment_score",""), d.get("positive_pct",""),
            d.get("negative_pct",""), d.get("neutral_pct",""),
            d.get("review_count",""),
            ", ".join(d.get("top_themes",[])),
            d.get("alert") or "",
        ]])

    # Competitor Alerts（追加）
    for a in report.get("competitor_alerts",[]):
        sheets_append(tok, "Competitor Alerts", [[
            date, a.get("brand",""), a.get("severity",""),
            a.get("issue",""), a.get("opportunity",""),
        ]])

    # Hot Topics（追加）
    for t in report.get("hot_topics",[]):
        sheets_append(tok, "Hot Topics", [[
            date, t.get("topic",""), t.get("volume",""),
            "是" if t.get("actionable") else "否",
            t.get("suggestion",""),
        ]])

    # Action Log（追加）
    for i, a in enumerate(report.get("actionable_top3",[]), 1):
        sheets_append(tok, "Action Log", [[date, f"優先 {i}", a, "待執行"]])

    print("[Sheets] 5 個工作表全部寫入完成")


# ── 4. GitHub Pages HTML ──────────────────────────────

def generate_html(report: dict):
    date = report.get("generated_at","")[:10]

    def brand_card(name, d):
        sc = d.get("sentiment_score",0)
        col = "#1D9E75" if sc>=0.7 else "#BA7517" if sc>=0.5 else "#E24B4A"
        own = any(b in name for b in ["ALUXE","JOY COLORi","acredo"])
        themes = "".join(f'<span class="tag">{t}</span>' for t in d.get("top_themes",[]))
        alert = (f'<div class="alert-box">{d["alert"]}</div>' if d.get("alert") else "")
        return f"""<div class="bc {'own' if own else 'comp'}">
          <div class="bh"><span class="bn">{name}</span>
            <span style="font-size:20px;font-weight:500;color:{col}">{sc:.2f}</span></div>
          <div class="bbar">
            <div style="width:{d.get('positive_pct',0)}%;background:#1D9E75"></div>
            <div style="width:{d.get('neutral_pct',0)}%;background:#D3D1C7"></div>
            <div style="width:{d.get('negative_pct',0)}%;background:#E24B4A"></div>
          </div>
          <div class="blbl">
            <span style="color:#1D9E75">正 {d.get('positive_pct',0)}%</span>
            <span style="color:#888">中 {d.get('neutral_pct',0)}%</span>
            <span style="color:#E24B4A">負 {d.get('negative_pct',0)}%</span>
          </div>
          <div class="themes">{themes}</div>{alert}</div>"""

    brands_html  = "".join(brand_card(n,d) for n,d in report.get("brands",{}).items())
    alerts_html  = "".join(f"""<div class="ai">
        <div style="font-size:13px;font-weight:500;color:{'#E24B4A' if a.get('severity',1)>=4 else '#BA7517'}">
          {a['brand']} <span style="font-size:11px;font-weight:400;margin-left:8px">嚴重度 {a.get('severity')}/5</span></div>
        <div style="font-size:13px;color:#3D3A32;margin:4px 0">{a['issue']}</div>
        <div style="font-size:12px;color:#1D9E75">機會：{a['opportunity']}</div></div>"""
        for a in report.get("competitor_alerts",[])) or '<p style="color:#7A7669;font-size:13px">本週無預警</p>'
    topics_html  = "".join(f"""<div class="ti">
        <div style="font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px;margin-bottom:4px">
          {t['topic']}
          {'<span class="act-badge">可介入</span>' if t.get('actionable') else ''}
          <span style="font-size:11px;color:{'#1D9E75' if t.get('volume')=='high' else '#BA7517'}">{t.get('volume','').upper()}</span>
        </div>
        <div style="font-size:12px;color:#7A7669">{t.get('suggestion','')}</div></div>"""
        for t in report.get("hot_topics",[]))
    actions_html = "".join(
        f'<div class="action-item"><span class="anum">{i+1}</span>{a}</div>'
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
.wrap{{max-width:1000px;margin:0 auto;padding:2rem 1.5rem}}
.summary{{background:var(--gold-l);border-left:3px solid var(--gold);border-radius:0 8px 8px 0;padding:1rem 1.25rem;font-size:14px;line-height:1.7;color:#3D3A32;margin-bottom:2rem}}
.sec{{margin-bottom:2.5rem}}
.sec-label{{font-size:11px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:#7A7669;margin-bottom:.75rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
.bc{{background:var(--card);border:.5px solid var(--border);border-radius:12px;padding:1.25rem}}
.bc.own{{border-color:var(--gold);border-width:1px}}
.bh{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
.bn{{font-size:13px;font-weight:500}}
.bbar{{display:flex;height:6px;border-radius:3px;overflow:hidden;margin-bottom:6px;gap:1px}}
.bbar div{{transition:width .3s}}
.blbl{{display:flex;gap:10px;font-size:11px;margin-bottom:8px}}
.themes{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}}
.tag{{background:#F1EFE8;color:#3D3A32;font-size:11px;padding:2px 8px;border-radius:20px}}
.alert-box{{background:#FFF0F0;color:#E24B4A;font-size:12px;padding:6px 10px;border-radius:6px;border-left:2px solid #E24B4A}}
.ai,.ti{{background:var(--card);border:.5px solid var(--border);border-radius:10px;padding:1rem 1.25rem;margin-bottom:8px}}
.act-badge{{background:#E1F5EE;color:#1D9E75;font-size:11px;padding:1px 8px;border-radius:20px;font-weight:400}}
.action-item{{display:flex;gap:12px;align-items:flex-start;padding:.875rem 1.25rem;background:var(--card);border:.5px solid var(--border);border-radius:10px;margin-bottom:8px;font-size:13px}}
.anum{{width:22px;height:22px;border-radius:50%;background:var(--dark);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}}
.ext-link{{display:inline-block;margin-top:.75rem;font-size:12px;color:var(--gold);text-decoration:none;border:.5px solid var(--gold);padding:4px 14px;border-radius:20px;margin-right:8px}}
footer{{text-align:center;font-size:11px;color:#7A7669;padding:2rem;border-top:.5px solid var(--border);margin-top:1rem}}
</style></head><body>
<header><h1>ALUXE · SG Sentiment Monitor</h1>
<span style="color:#7A7669;font-size:12px">報告日期：{date}</span></header>
<div class="wrap">
  <div class="summary">{report.get('summary','')}</div>
  <div class="sec"><div class="sec-label">品牌情感分析</div><div class="grid">{brands_html}</div></div>
  <div class="sec"><div class="sec-label">競品負評預警</div>{alerts_html}</div>
  <div class="sec"><div class="sec-label">熱門議題 · 可操作清單</div>{topics_html}</div>
  <div class="sec">
    <div class="sec-label">本週優先行動 Top 3</div>
    {actions_html}
    <a class="ext-link" href="https://docs.google.com/spreadsheets/d/{SHEETS_ID}" target="_blank">歷史數據 Google Sheets →</a>
  </div>
</div>
<footer>ALUXE Marketing Intelligence · Claude + Apify · {date}</footer>
</body></html>"""

    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
    print("[HTML] index.html 產生完成")


# ── 5. Telegram ───────────────────────────────────────

def send_telegram(report: dict):
    date = report.get("generated_at","")[:10]
    own  = [v["sentiment_score"] for k,v in report.get("brands",{}).items()
            if any(b in k for b in ["ALUXE","JOY COLORi","acredo"])]
    avg  = round(sum(own)/len(own),2) if own else 0
    icon = "📈" if avg>=0.7 else "📊" if avg>=0.5 else "📉"

    alerts = "\n".join(
        f"{'🔴' if a.get('severity',1)>=4 else '🟡'} {a['brand']} — {a['issue']}"
        for a in report.get("competitor_alerts",[])
    ) or "本週無預警"
    actions = "\n".join(
        f"{i+1}. {a}" for i,a in enumerate(report.get("actionable_top3",[]))
    )

    msg = (f"ALUXE SG 輿情週報 · {date}\n\n"
           f"{icon} 自家品牌平均分：{avg}\n\n"
           f"競品預警\n{alerts}\n\n"
           f"本週優先行動\n{actions}\n\n"
           f"儀表板：{PAGES_URL}\n"
           f"數據：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
        timeout=30,
    ).raise_for_status()
    print("[Telegram] 推送完成")


# ── 6. 本地 JSON 存檔 ─────────────────────────────────

def save_json(report: dict):
    hf = OUTPUT_DIR / "history.json"
    history = json.loads(hf.read_text()) if hf.exists() else []
    history.insert(0, report)
    hf.write_text(json.dumps(history[:26], ensure_ascii=False, indent=2))
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("[JSON] 存檔完成")


# ── 主程式 ────────────────────────────────────────────

def main():
    print(f"\n{'='*52}")
    print(f"  ALUXE SG Sentiment  —  {datetime.date.today()}")
    print(f"{'='*52}\n")

    data   = fetch_data()
    report = analyze(data)

    save_json(report)
    generate_html(report)
    write_sheets(report)
    send_telegram(report)

    print(f"\n✅ 完成")
    print(f"   網頁：{PAGES_URL}")
    print(f"   試算表：https://docs.google.com/spreadsheets/d/{SHEETS_ID}")


if __name__ == "__main__":
    main()
