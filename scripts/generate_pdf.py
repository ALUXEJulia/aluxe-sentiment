#!/usr/bin/env python3
"""
ALUXE 競品市場週報 PDF 產生器

從 docs/{market}_latest.json 讀取分析結果，渲染成 HTML，
再用 Chromium headless 轉成 PDF（macOS 與 Ubuntu Runner 都支援）。

使用方式：
    python scripts/generate_pdf.py <market> [<json_path>] [<pdf_path>]
    例： python scripts/generate_pdf.py sg
         python scripts/generate_pdf.py sg docs/sg_latest.json docs/sg_report.pdf

也可以當模組呼叫：
    from scripts.generate_pdf import generate_pdf
    pdf_path = generate_pdf("sg")
"""
import json
import base64
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
OUTPUT_DIR = REPO_ROOT / "docs"

LOGO_WHITE = ASSETS_DIR / "aluxe-logo-white.png"
LOGO_BLACK = ASSETS_DIR / "aluxe-logo-black.png"

MARKET_LABELS = {
    "sg": "🇸🇬 Singapore",
    "hk": "🇭🇰 Hong Kong",
}


def find_chrome() -> str:
    """跨平台找 Chrome/Chromium 執行檔"""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
        path = shutil.which(name)
        if path:
            return path
    raise FileNotFoundError(
        "找不到 Chrome/Chromium。macOS 請從官網安裝；Ubuntu 用 "
        "`sudo apt-get install -y chromium-browser` 或在 GitHub Actions 加 "
        "`browser-actions/setup-chrome@v1`。"
    )


def img_to_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


# ─────────────────────────────────────────────────────────
# HTML 樣板
# ─────────────────────────────────────────────────────────

def severity_badge(level: int) -> str:
    levels = {
        4: ("⚠️ 高度警示", "#c0392b"),
        3: ("⚡ 中度警示", "#e67e22"),
        2: ("🔶 注意",   "#f39c12"),
        1: ("ℹ️ 輕微",   "#95a5a6"),
    }
    label, color = levels.get(level, ("—", "#999"))
    return f'<span class="severity" style="background:{color}">{label} · Lv.{level}</span>'


def sentiment_bar(score: float) -> str:
    pct = int(score * 100)
    color = "#27ae60" if score >= 0.9 else "#f39c12" if score >= 0.7 else "#e74c3c"
    return f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%; background:{color}"></div></div>'


def render_thumb(sample: dict, idx: int) -> str:
    """sample = {image_url, ad_library_url, start_date, title}; 沒圖片就退化為佔位框"""
    url = (sample or {}).get("image_url") or ""
    if url:
        return (
            f'<div class="thumb">'
            f'<img src="{url}" alt="廣告 #{idx+1}" />'
            f'</div>'
        )
    return f'<div class="thumb-placeholder">廣告 #{idx+1}<br><small>無素材</small></div>'


def render_html(d: dict, market: str) -> str:
    market_label = MARKET_LABELS.get(market, market.upper())
    logo_white = img_to_data_uri(LOGO_WHITE)
    logo_black = img_to_data_uri(LOGO_BLACK)

    # 報告週期：優先讀 JSON 內的 report_period，沒有時 fallback 從 generated_at 推算
    period = d.get("report_period") or {}
    if period.get("week_start") and period.get("week_end"):
        week_mon_str = period["week_start"]
        week_sun_str = period["week_end"]
        week_iso = period.get("iso_week", datetime.fromisoformat(week_mon_str).isocalendar()[1])
    else:
        gen = datetime.fromisoformat(d["generated_at"])
        weekday = gen.weekday()
        last_sun = gen - timedelta(days=1 if weekday == 0 else weekday + 1)
        last_mon = last_sun - timedelta(days=6)
        week_mon_str = last_mon.date().isoformat()
        week_sun_str = last_sun.date().isoformat()
        week_iso = last_mon.isocalendar()[1]

    period_str = f"Week {week_iso} · {week_mon_str} ~ {week_sun_str}"
    generated = datetime.fromisoformat(d["generated_at"])

    # ── 封面 ──
    cover = f'''
    <section class="page cover">
      <div class="cover-frame">
        <img src="{logo_white}" class="brand-logo" alt="ALUXE">
        <div class="report-tag">{market.upper()} Market Intelligence Report</div>
        <h1>競品市場週報</h1>
        <div class="meta">
          <div><strong>市場</strong>　{market_label}</div>
          <div><strong>週次</strong>　{period_str}</div>
          <div><strong>分析品牌</strong>　<span class="brand-list">{
            " / ".join(f"<span>{b}</span>" for b in d.get("brands", {}).keys())
          }</span></div>
        </div>
        <div class="confidential">CONFIDENTIAL · INTERNAL USE ONLY</div>
      </div>
    </section>
    '''

    # ── 執行摘要 ──
    own_brand_key = next((k for k in d["brands"] if "ALUXE" in k), None)
    own_score = d["brands"].get(own_brand_key, {}).get("sentiment_score", 0) if own_brand_key else 0
    competitors = [b for k, b in d["brands"].items() if k != own_brand_key]
    competitor_avg = sum(b["sentiment_score"] for b in competitors) / len(competitors) if competitors else 0
    diff = own_score - competitor_avg

    summary_section = f'''
    <section class="page">
      <h2 class="section-title">執行摘要 <span class="window-tag primary">📅 報告週 · Mon-Sun</span></h2>
      <div class="period-bar">📅 報告週期 · <strong>{period_str}</strong>　|　Mon 00:00 ~ Sun 23:59 SGT</div>
      <div class="kpi-row">
        <div class="kpi">
          <div class="kpi-label">ALUXE 情感分數</div>
          <div class="kpi-value">{own_score:.2f}</div>
          <div class="kpi-sub">{"優於" if diff>=0 else "低於"}競品平均 {abs(diff)*100:.1f} 分</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">競品平均</div>
          <div class="kpi-value">{competitor_avg:.2f}</div>
          <div class="kpi-sub">{len(competitors)} 家品牌</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">本週警示</div>
          <div class="kpi-value">{len(d.get("competitor_alerts",[]))}</div>
          <div class="kpi-sub">含 {sum(1 for a in d.get("competitor_alerts",[]) if a["severity"]>=3)} 件中高度</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">熱門議題</div>
          <div class="kpi-value">{sum(1 for h in d.get("hot_topics",[]) if h.get("actionable"))}</div>
          <div class="kpi-sub">可立即行動</div>
        </div>
      </div>

      <h3 class="sub-title">市場全貌</h3>
      <p class="prose">{d.get("summary","")}</p>

      <h3 class="sub-title">本週三大行動建議</h3>
      <ol class="action-list">
        {''.join(f'<li><div class="action-item">{a}</div></li>' for a in d.get("actionable_top3", []))}
      </ol>
    </section>
    '''

    # ── 品牌情感分析 ──
    brand_rows = []
    for name, b in d["brands"].items():
        is_own = name == own_brand_key
        brand_rows.append(f'''
        <div class="brand-card {"own" if is_own else ""}">
          <div class="brand-head">
            <span class="brand-name">{"⭐ " if is_own else ""}{name}</span>
            <span class="brand-score">{b["sentiment_score"]:.2f}</span>
          </div>
          {sentiment_bar(b["sentiment_score"])}
          <div class="brand-stats">
            <span>👍 {b.get("positive_pct",0)}%</span>
            <span>😐 {b.get("neutral_pct",0)}%</span>
            <span>👎 {b.get("negative_pct",0)}%</span>
            <span>💬 {b.get("review_count",0)} 則</span>
          </div>
          <div class="themes">
            <strong>關鍵主題</strong>
            <ul>{''.join(f"<li>{t}</li>" for t in b.get("top_themes", []))}</ul>
          </div>
          {f'<div class="sample-quote">「{b["sample_positive"][:140]}...」</div>' if b.get("sample_positive") else ""}
        </div>
        ''')

    brand_section = f'''
    <section class="page">
      <h2 class="section-title">品牌情感分析 <span class="window-tag primary">📅 本報告週 Mon-Sun</span></h2>
      <p class="lede">本週共分析 {sum(b.get("review_count",0) for b in d["brands"].values())} 則評論 · 涵蓋 {len(d["brands"])} 個品牌 · 資料來源：Google Maps</p>
      <div class="brand-grid">
        {''.join(brand_rows)}
      </div>
    </section>
    '''

    # ── 競品廣告戰報 ──
    ads_rows = []
    capped_brands = []
    for name, ad in d["competitor_ads"].items():
        is_own = ad.get("own", False)
        themes_html = "".join(f"<li>{t}</li>" for t in ad.get("main_themes", []))
        offers_html = "".join(f"<li>{o}</li>" for o in ad.get("key_offers", [])[:3])
        platforms = " · ".join(ad.get("platforms", []))
        insight = ad.get("strategy_insight", "").split("\n")[0][:280] + ("..." if ad.get("strategy_insight") else "")

        # 縮圖：用實際 sample_ads，不夠 4 個就補佔位框
        samples = ad.get("sample_ads", []) or []
        thumbs = "".join(render_thumb(samples[i] if i < len(samples) else None, i) for i in range(4))

        capped = ad.get("ad_count", 0) >= 30
        if capped:
            capped_brands.append(name)
        cap_tag = '<span class="cap-warning">🔒 達上限</span>' if capped else ''

        ads_rows.append(f'''
        <div class="ads-card {"own" if is_own else ""}">
          <div class="ads-head">
            <span class="brand-name">{"⭐ " if is_own else ""}{name}</span>
            <span class="ad-count-wrap">
              <span class="ad-count">{ad.get("ad_count",0)} 則廣告</span>
              {cap_tag}
            </span>
          </div>
          <div class="ad-thumbs">{thumbs}</div>
          <div class="ads-body">
            <div class="ads-col">
              <strong>廣告主軸</strong>
              <ul>{themes_html}</ul>
            </div>
            <div class="ads-col">
              <strong>主要優惠</strong>
              <ul>{offers_html}</ul>
            </div>
          </div>
          <div class="ads-meta">
            <span><strong>CTA</strong>：{ad.get("cta_focus", "")[:80]}</span>
            <span><strong>平台</strong>：{platforms}</span>
          </div>
          <div class="insight">
            <strong>💡 戰略洞察</strong>
            <p>{insight}</p>
          </div>
        </div>
        ''')

    cap_note = (
        f'<div class="data-disclaimer">⚠️ <strong>抓取限制說明</strong>：'
        f'Apify 每品牌抓取上限約 30 則，標示「🔒 達上限」的 {len(capped_brands)} 個品牌'
        f'（{"、".join(capped_brands)}）<strong>實際投放數可能更多</strong>，本報告僅顯示抓取到的樣本。</div>'
        if capped_brands else ''
    )
    ads_section = f'''
    <section class="page">
      <h2 class="section-title">競品廣告戰報 <span class="window-tag rolling">📅 過去 30 天活躍廣告（滾動）</span></h2>
      <p class="lede">本週共觀察 {sum(a.get("ad_count",0) for a in d["competitor_ads"].values())} 則 Meta 廣告投放 · 資料來源：Meta Ads Library</p>
      {cap_note}
      <div class="ads-grid">
        {''.join(ads_rows)}
      </div>
    </section>
    '''

    # ── 警示 + 議題 + 趨勢 + 末頁 footer ──
    alerts = sorted(d.get("competitor_alerts", []), key=lambda x: -x.get("severity", 0))
    alert_rows = "".join(f'''
      <div class="alert-card sev-{a.get("severity",1)}">
        <div class="alert-head">
          <span class="alert-brand">{a.get("brand","")}</span>
          {severity_badge(a.get("severity",1))}
        </div>
        <div class="alert-issue"><strong>威脅</strong>：{a.get("issue","")}</div>
        <div class="alert-opp"><strong>應對策略</strong>：{a.get("opportunity","")}</div>
      </div>
    ''' for a in alerts)

    topics = [t for t in d.get("hot_topics", []) if t.get("actionable")]
    topic_rows = "".join(f'''
      <div class="topic-card vol-{t.get("volume","medium")}">
        <div class="topic-head">
          <span class="topic-name">{t.get("topic","")}</span>
          <span class="topic-vol">{t.get("volume","").upper()}</span>
        </div>
        <p class="topic-sug">{t.get("suggestion","")[:200]}{"..." if len(t.get("suggestion",""))>200 else ""}</p>
      </div>
    ''' for t in topics)

    trend_rows = "".join(f'''
      <tr>
        <td><strong>{t.get("keyword","")}</strong></td>
        <td><span class="trend-tag {t.get("trend","")}">{t.get("trend","")}</span></td>
        <td>{t.get("insight","")[:150]}{"..." if len(t.get("insight",""))>150 else ""}</td>
      </tr>
    ''' for t in d.get("market_trends", [])[:5])

    last_section = f'''
    <section class="page">
      <h2 class="section-title">競品威脅警示 <span class="window-tag mixed">📅 跨資料源綜合判斷</span></h2>
      <div class="alert-grid">{alert_rows}</div>
    </section>

    <section class="page">
      <h2 class="section-title">熱門議題 · 可行動機會 <span class="window-tag rolling">📅 過去 28 天 Google Trends（滾動）</span></h2>
      <div class="topic-grid">{topic_rows}</div>
    </section>

    <section class="page last-page">
      <h2 class="section-title">搜尋趨勢追蹤 <span class="window-tag rolling">📅 過去 28 天 Trends + GSC（滾動）</span></h2>
      <table class="trend-table">
        <thead>
          <tr><th style="width:25%">關鍵字</th><th style="width:12%">趨勢</th><th>洞察</th></tr>
        </thead>
        <tbody>{trend_rows}</tbody>
      </table>

      <div class="report-footer">
        <img src="{logo_black}" class="footer-logo" alt="ALUXE">
        <div class="footer-block">
          <div class="footer-row">
            <strong>📅 報告週期</strong>
            <span>{period_str}</span>
          </div>
          <div class="footer-row sub">
            <strong></strong>
            <span class="indent">└ <em>品牌情感分析</em>　對齊本週（Mon 00:00 ~ Sun 23:59 SGT）</span>
          </div>
          <div class="footer-row sub">
            <strong></strong>
            <span class="indent">└ <em>競品廣告戰報</em>　過去 30 天活躍廣告（滾動視窗）⚠️ Apify 抓取上限約 30 則/品牌</span>
          </div>
          <div class="footer-row sub">
            <strong></strong>
            <span class="indent">└ <em>熱門議題 / 趨勢</em>　過去 28 天 Google Trends（滾動視窗）</span>
          </div>
          <div class="footer-row sub">
            <strong></strong>
            <span class="indent">└ <em>搜尋表現</em>　　　過去 28 天 Google Search Console（滾動視窗）</span>
          </div>
          <div class="footer-row">
            <strong>🔄 更新頻率</strong>
            <span>每週一 06:00 SGT 自動執行 · 每週一份完整週報</span>
          </div>
          <div class="footer-row">
            <strong>📊 資料來源</strong>
            <span>Google Maps · Meta Ads Library · Google Trends · Google Search Console</span>
          </div>
          <div class="footer-row">
            <strong>⚙️ 產製時間</strong>
            <span>{generated.strftime('%Y-%m-%d %H:%M')} UTC · ALUXE 競品市場週報 v6</span>
          </div>
        </div>
        <div class="footer-confidential">CONFIDENTIAL · 本報告僅供 ALUXE 集團內部使用</div>
      </div>
    </section>
    '''

    return f'''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>ALUXE 競品市場週報 · {market.upper()} {period_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;600;700&family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    font-family: "Noto Sans TC", "PingFang TC", "Heiti TC", sans-serif;
    color: #2c2c2c; line-height: 1.6; font-size: 10.5pt;
  }}
  h1, h2, h3 {{
    font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", "Heiti TC", sans-serif;
    font-weight: 700; color: #1a1f3a; letter-spacing: 0.3px;
  }}
  .page {{ page-break-after: always; min-height: 95vh; }}
  .last-page {{ page-break-after: auto; }}

  .cover {{
    background: linear-gradient(135deg, #1a1f3a 0%, #2a3050 100%);
    color: #fdfaf3; display: flex; align-items: center; justify-content: center;
    margin: -18mm -16mm; padding: 18mm 16mm; min-height: 95vh;
  }}
  .cover-frame {{ border: 1px solid #c9a961; padding: 60px 50px; text-align: center; width: 100%; }}
  .brand-logo {{ display: block; margin: 0 auto 10px; width: 280px; height: auto; }}
  .report-tag {{ font-size: 9pt; letter-spacing: 4px; color: #c9a961; text-transform: uppercase; margin-bottom: 50px; }}
  .cover h1 {{ color: #fdfaf3; font-size: 32pt; margin: 0 0 50px 0; font-weight: 500; letter-spacing: 6px; }}
  .brand-list span {{ white-space: nowrap; }}
  .cover .meta {{ text-align: left; max-width: 560px; margin: 0 auto; font-size: 10.5pt; line-height: 2.2; }}
  .cover .meta > div {{ white-space: nowrap; }}
  .cover .meta strong {{ display: inline-block; width: 80px; color: #c9a961; font-weight: 400; }}
  .confidential {{ margin-top: 60px; font-size: 8pt; letter-spacing: 3px; color: #c9a961; opacity: 0.7; }}

  .section-title {{
    font-size: 18pt; border-bottom: 2px solid #c9a961; padding-bottom: 6px; margin: 0 0 12px 0;
    display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  }}
  .window-tag {{ font-family: "Noto Sans TC", sans-serif; font-weight: 400; font-size: 9pt; padding: 3px 10px; border-radius: 12px; letter-spacing: 0.3px; white-space: nowrap; }}
  .window-tag.primary {{ background: #c9a961; color: #1a1f3a; }}
  .window-tag.rolling {{ background: #e8e2d2; color: #5a4a20; }}
  .window-tag.mixed {{ background: #f0e9d8; color: #7a5a30; border: 1px dashed #c9a961; }}

  .data-disclaimer {{ background: #fff8e1; border: 1px solid #f0d090; border-left: 4px solid #e67e22;
    padding: 8px 12px; margin: 0 0 12px 0; border-radius: 3px; font-size: 9pt; line-height: 1.6; color: #6a4a10; }}
  .data-disclaimer strong {{ color: #b8520a; }}

  .sub-title {{ font-size: 12pt; color: #1a1f3a; margin: 22px 0 10px 0; border-left: 3px solid #c9a961; padding-left: 10px; }}
  .lede {{ color: #666; font-size: 9.5pt; margin: -6px 0 14px 0; }}
  .period-bar {{ background: #1a1f3a; color: #fdfaf3; padding: 8px 14px; border-radius: 3px; font-size: 9.5pt; margin-bottom: 14px; letter-spacing: 0.5px; }}
  .period-bar strong {{ color: #c9a961; }}
  .prose {{ text-align: justify; background: #fdfaf3; padding: 14px 16px; border-radius: 4px; border-left: 3px solid #c9a961; }}

  .kpi-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }}
  .kpi {{ background: #1a1f3a; color: #fdfaf3; padding: 14px 12px; border-radius: 4px; text-align: center; }}
  .kpi-label {{ font-size: 8pt; color: #c9a961; text-transform: uppercase; letter-spacing: 1.5px; }}
  .kpi-value {{ font-size: 26pt; font-weight: 700; line-height: 1.2; margin: 4px 0; font-family: "Noto Serif TC", serif; }}
  .kpi-sub {{ font-size: 8pt; color: rgba(253,250,243,0.7); }}

  .action-list {{ counter-reset: action; padding-left: 0; list-style: none; }}
  .action-list li {{ counter-increment: action; margin-bottom: 12px; padding-left: 36px; position: relative; }}
  .action-list li::before {{ content: counter(action); position: absolute; left: 0; top: 0; width: 26px; height: 26px;
    background: #c9a961; color: #1a1f3a; border-radius: 50%; text-align: center; line-height: 26px; font-weight: 700; font-family: "Noto Serif TC", serif; }}
  .action-item {{ background: #fff; padding: 10px 14px; border-left: 3px solid #c9a961; font-size: 9.5pt; line-height: 1.7; }}

  .brand-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .brand-card {{ border: 1px solid #e0d4b8; border-radius: 4px; padding: 12px 14px; background: #fff; }}
  .brand-card.own {{ background: #fdfaf3; border-color: #c9a961; border-width: 2px; }}
  .brand-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }}
  .brand-name {{ font-weight: 700; font-size: 11pt; color: #1a1f3a; }}
  .brand-score {{ font-family: "Noto Serif TC", serif; font-size: 16pt; color: #c9a961; font-weight: 700; }}
  .bar-track {{ background: #f0e9d8; height: 8px; border-radius: 4px; margin: 4px 0 8px; }}
  .bar-fill {{ height: 100%; border-radius: 4px; }}
  .brand-stats {{ display: flex; gap: 8px; font-size: 8.5pt; color: #666; margin-bottom: 8px; }}
  .themes strong {{ font-size: 8.5pt; color: #c9a961; text-transform: uppercase; letter-spacing: 1px; }}
  .themes ul {{ margin: 4px 0 8px 0; padding-left: 18px; font-size: 9pt; }}
  .themes li {{ margin-bottom: 2px; }}
  .sample-quote {{ font-size: 8.5pt; color: #777; font-style: italic; padding: 8px 10px; background: #faf6ec; border-left: 2px solid #c9a961; line-height: 1.5; }}

  .ads-grid {{ display: flex; flex-direction: column; gap: 10px; }}
  .ads-card {{ border: 1px solid #e0d4b8; border-radius: 4px; padding: 12px 14px; page-break-inside: avoid; }}
  .ads-card.own {{ background: #fdfaf3; border-color: #c9a961; border-width: 2px; }}
  .ads-head {{ display: flex; justify-content: space-between; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px dashed #ddd; }}
  .ad-count {{ background: #1a1f3a; color: #fdfaf3; padding: 2px 10px; border-radius: 12px; font-size: 8.5pt; }}
  .ad-count-wrap {{ display: flex; gap: 6px; align-items: center; }}
  .cap-warning {{ background: #e67e22; color: white; padding: 2px 8px; border-radius: 10px; font-size: 7.5pt; letter-spacing: 0.5px; font-weight: 600; }}

  .ad-thumbs {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 6px 0 10px; }}
  .thumb {{ aspect-ratio: 1 / 1; border: 1px solid #e0d4b8; border-radius: 3px; overflow: hidden; background: #fdfaf3; }}
  .thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .thumb-placeholder {{ aspect-ratio: 1 / 1; background:
    repeating-linear-gradient(45deg, transparent, transparent 8px, rgba(201,169,97,0.08) 8px, rgba(201,169,97,0.08) 16px), #faf6ec;
    border: 1px dashed #c9a961; border-radius: 3px; display: flex; flex-direction: column; align-items: center; justify-content: center;
    font-size: 8pt; color: #c9a961; text-align: center; font-weight: 600; line-height: 1.3; }}
  .thumb-placeholder small {{ color: #b89844; font-weight: 400; font-size: 7pt; }}

  .ads-body {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 8px; }}
  .ads-col strong {{ font-size: 8.5pt; color: #c9a961; text-transform: uppercase; letter-spacing: 1px; }}
  .ads-col ul {{ margin: 3px 0; padding-left: 16px; font-size: 9pt; line-height: 1.5; }}
  .ads-meta {{ font-size: 8.5pt; color: #555; border-top: 1px dashed #ddd; padding-top: 6px; margin-bottom: 6px; }}
  .ads-meta span {{ display: block; margin-bottom: 2px; }}
  .insight {{ background: #faf6ec; padding: 8px 10px; border-radius: 3px; font-size: 9pt; }}
  .insight strong {{ color: #c9a961; }}
  .insight p {{ margin: 4px 0 0 0; line-height: 1.6; }}

  .alert-grid {{ display: flex; flex-direction: column; gap: 10px; }}
  .alert-card {{ padding: 12px 14px; border-radius: 4px; border-left: 4px solid #999; page-break-inside: avoid; }}
  .alert-card.sev-4 {{ background: #fef2ef; border-left-color: #c0392b; }}
  .alert-card.sev-3 {{ background: #fef6ed; border-left-color: #e67e22; }}
  .alert-card.sev-2 {{ background: #fef9ed; border-left-color: #f39c12; }}
  .alert-card.sev-1 {{ background: #f5f5f5; border-left-color: #95a5a6; }}
  .alert-head {{ display: flex; justify-content: space-between; margin-bottom: 6px; }}
  .alert-brand {{ font-weight: 700; color: #1a1f3a; font-size: 11pt; }}
  .severity {{ color: white; padding: 2px 10px; border-radius: 12px; font-size: 8pt; }}
  .alert-issue, .alert-opp {{ font-size: 9.5pt; line-height: 1.6; margin-bottom: 4px; }}
  .alert-opp {{ margin-top: 4px; }}
  .alert-issue strong {{ color: #c0392b; }}
  .alert-opp strong {{ color: #27ae60; }}

  .topic-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .topic-card {{ border: 1px solid #e0d4b8; padding: 10px 12px; border-radius: 4px; page-break-inside: avoid; }}
  .topic-head {{ display: flex; justify-content: space-between; margin-bottom: 6px; }}
  .topic-name {{ font-weight: 600; color: #1a1f3a; font-size: 10pt; }}
  .topic-vol {{ font-size: 7.5pt; padding: 1px 8px; border-radius: 8px; background: #c9a961; color: #1a1f3a; font-weight: 700; }}
  .topic-card.vol-medium .topic-vol {{ background: #ddd; color: #555; }}
  .topic-sug {{ font-size: 9pt; color: #555; line-height: 1.5; margin: 0; }}

  .trend-table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
  .trend-table th {{ background: #1a1f3a; color: #fdfaf3; padding: 10px; text-align: left; font-size: 9pt; letter-spacing: 1px; }}
  .trend-table td {{ padding: 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
  .trend-table tr:nth-child(even) {{ background: #fdfaf3; }}
  .trend-tag {{ padding: 2px 8px; border-radius: 8px; font-size: 8pt; background: #27ae60; color: white; }}

  .report-footer {{ margin-top: 30px; padding: 20px 24px; border: 1px solid #e0d4b8; border-radius: 4px; background: #fdfaf3; page-break-inside: avoid; }}
  .footer-logo {{ display: block; width: 120px; height: auto; margin: 0 auto 14px; opacity: 0.85; }}
  .footer-block {{ border-top: 1px solid #c9a961; padding-top: 12px; }}
  .footer-row {{ display: flex; gap: 14px; font-size: 9pt; line-height: 1.7; color: #444; padding: 3px 0; }}
  .footer-row strong {{ flex: 0 0 100px; color: #1a1f3a; font-weight: 600; }}
  .footer-row.sub {{ font-size: 8.5pt; color: #777; padding: 2px 0; line-height: 1.5; }}
  .footer-row.sub .indent {{ flex: 1; }}
  .footer-row.sub em {{ font-style: normal; font-weight: 600; color: #1a1f3a; margin-right: 6px; }}
  .footer-confidential {{ margin-top: 12px; padding-top: 10px; border-top: 1px dashed #d0c098; text-align: center; font-size: 8pt; letter-spacing: 2px; color: #999; }}
</style>
</head>
<body>
{cover}{summary_section}{brand_section}{ads_section}{last_section}
</body>
</html>'''


# ─────────────────────────────────────────────────────────
# 主函式
# ─────────────────────────────────────────────────────────

def generate_pdf(market: str, json_path: Path = None, pdf_path: Path = None) -> Path:
    """產生 PDF。回傳 PDF 檔案路徑。"""
    market = market.lower()
    json_path = Path(json_path) if json_path else OUTPUT_DIR / f"{market}_latest.json"
    pdf_path  = Path(pdf_path)  if pdf_path  else OUTPUT_DIR / f"{market}_report.pdf"
    html_path = pdf_path.with_suffix(".html")

    print(f"[PDF] 產生 {market.upper()} 週報...")
    print(f"  JSON: {json_path}")

    if not json_path.exists():
        raise FileNotFoundError(f"找不到資料：{json_path}")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    html = render_html(data, market)
    html_path.write_text(html, encoding="utf-8")
    print(f"  HTML: {html_path}")

    chrome = find_chrome()
    print(f"  Chrome: {chrome}")
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-sandbox",
        "--virtual-time-budget=15000", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", f"file://{html_path.resolve()}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"Chrome PDF 產生失敗：\n{result.stderr}")

    print(f"  ✅ PDF: {pdf_path} ({pdf_path.stat().st_size / 1024:.0f} KB)")
    return pdf_path


def main():
    if len(sys.argv) < 2:
        print("使用方式：python scripts/generate_pdf.py <market> [<json_path>] [<pdf_path>]")
        print("市場：sg / hk")
        sys.exit(1)
    market = sys.argv[1]
    json_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    pdf_path  = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    generate_pdf(market, json_path, pdf_path)


if __name__ == "__main__":
    main()
