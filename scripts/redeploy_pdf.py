#!/usr/bin/env python3
"""
ALUXE 競品週報 PDF 補發腳本

只做 3 件事：
  1. 用現有的 docs/{market}_latest.json 重新產 PDF
  2. 上傳到 Google Drive
  3. 發一則完整週報摘要 Telegram 訊息（含 Drive 連結）

跳過 Apify、Claude、Sheets 等所有花錢的步驟。

使用方式：
  python scripts/redeploy_pdf.py [sg|hk]
"""
import os
import json
import sys
from pathlib import Path

import requests

# 把 scripts/ 加進 path 才能 import 同層的模組
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_pdf import generate_pdf
from upload_drive import upload_pdf_to_drive


def build_telegram_summary(data: dict, market: str, drive_url: str, pages_url: str, sheets_id: str) -> str:
    """
    根據 JSON 資料建構與主流程 send_telegram() 一致的週報摘要。
    多了 (PDF 補發) 標記避免跟主流程的訊息混淆。
    """
    date = data.get("generated_at", "")[:10]
    market_upper = market.upper()

    # 自家品牌平均分
    own = [v["sentiment_score"] for k, v in data.get("brands", {}).items()
           if "ALUXE" in k and "JOY" not in k]
    avg = round(sum(own) / len(own), 2) if own else 0
    icon = "📈" if avg >= 0.7 else "📊" if avg >= 0.5 else "📉"

    # 競品負評預警
    alerts = "\n".join(
        f"{'🔴' if a.get('severity', 1) >= 4 else '🟡'} {a['brand']} — {a['issue']}"
        for a in data.get("competitor_alerts", [])
    ) or "本週無預警"

    # 三大行動
    actions = "\n".join(
        f"{i+1}. {a}" for i, a in enumerate(data.get("actionable_top3", []))
    )

    # 廣告摘要：分自家 + 競品兩段
    own_ads_summary = ""
    comp_ads_summary = ""
    for brand, ad_data in data.get("competitor_ads", {}).items():
        count = ad_data.get("ad_count", 0)
        focus = ad_data.get("cta_focus", "")
        line = f"\n· {brand}：{count} 則廣告，主打「{focus}」"
        if ad_data.get("own", False):
            own_ads_summary += line
        else:
            comp_ads_summary += line
    own_ads_section = f"自家廣告動態{own_ads_summary}\n\n" if own_ads_summary else ""
    comp_ads_section = (
        f"競品廣告動態（每品牌取最新+最舊代表樣本分析）{comp_ads_summary}\n\n"
        if comp_ads_summary else ""
    )

    # GSC
    gsc = data.get("gsc_insights", {})
    gsc_line = ""
    if gsc.get("top_keywords"):
        top3 = ", ".join(k["keyword"] for k in gsc["top_keywords"][:3])
        gsc_line = f"\n\n🔍 GSC 前3關鍵字：{top3}"
    if gsc.get("opportunities"):
        gsc_line += f"\n⚡ 機會點：{gsc['opportunities'][0]['keyword']}"

    pdf_line = f"\n\n📄 PDF 完整週報：{drive_url}" if drive_url else ""
    dashboard_line = f"\n儀表板：{pages_url}" if pages_url else ""
    sheets_line = f"\n數據：https://docs.google.com/spreadsheets/d/{sheets_id}" if sheets_id else ""

    return (
        f"ALUXE {market_upper} 競品市場週報（PDF 補發）· {date}\n\n"
        f"{icon} 自家品牌平均分：{avg}"
        f"{gsc_line}\n\n"
        f"競品負評預警\n{alerts}\n\n"
        f"{own_ads_section}"
        f"{comp_ads_section}"
        f"本週優先行動\n{actions}"
        f"{pdf_line}\n"
        f"{dashboard_line}{sheets_line}"
    )


def main():
    market = (sys.argv[1] if len(sys.argv) > 1 else "sg").lower()
    if market not in ("sg", "hk"):
        print(f"❌ 未知市場：{market}（請用 sg 或 hk）")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    json_path = repo_root / "docs" / f"{market}_latest.json"
    if not json_path.exists():
        print(f"❌ 找不到資料檔：{json_path}")
        print(f"   請先跑一次 run_analysis_{market}.py 產出 JSON")
        sys.exit(1)

    SERVICE_ACCOUNT = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
    PAGES_URL = os.environ.get("PAGES_URL", "")
    SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "")

    if not FOLDER_ID:
        print("❌ 環境變數 GOOGLE_DRIVE_FOLDER_ID 未設定")
        sys.exit(1)

    # 讀 JSON 取週次資訊
    data = json.loads(json_path.read_text(encoding="utf-8"))
    period = data.get("report_period", {})
    iso_week = period.get("iso_week", "XX")
    week_start = period.get("week_start", "")
    week_end = period.get("week_end", "")

    week_label = (
        f"W{iso_week:02d}_{week_start}_to_{week_end}"
        if isinstance(iso_week, int) else f"WXX_{week_start}_to_{week_end}"
    )
    pdf_path = repo_root / "docs" / f"ALUXE_{market.upper()}_週報_{week_label}.pdf"

    # 1. PDF
    print("=" * 50)
    print(f"  ALUXE {market.upper()} PDF 補發")
    print("=" * 50)
    generate_pdf(market, json_path=json_path, pdf_path=pdf_path)

    # 2. Drive
    info = upload_pdf_to_drive(pdf_path, FOLDER_ID, SERVICE_ACCOUNT)
    drive_url = info.get("webViewLink", "")

    # 3. Telegram（完整週報摘要 + Drive 連結）
    if TG_TOKEN and TG_CHAT and drive_url:
        msg = build_telegram_summary(data, market, drive_url, PAGES_URL, SHEETS_ID)
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": msg},
                timeout=30,
            ).raise_for_status()
            print("[Telegram] 完整週報摘要已送出（含 PDF 連結）")
        except Exception as e:
            print(f"[Telegram] 訊息送出失敗（不影響 PDF 已上 Drive）：{e}")

    print()
    print(f"✅ 完成")
    print(f"   PDF：{pdf_path.name}")
    print(f"   Drive：{drive_url}")


if __name__ == "__main__":
    main()
