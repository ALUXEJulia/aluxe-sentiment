#!/usr/bin/env python3
"""
ALUXE 競品週報 PDF 補發腳本

只做 3 件事：
  1. 用現有的 docs/{market}_latest.json 重新產 PDF
  2. 上傳到 Google Drive
  3. 發一則簡短 Telegram 訊息（含 Drive 連結）

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

    if not FOLDER_ID:
        print("❌ 環境變數 GOOGLE_DRIVE_FOLDER_ID 未設定")
        sys.exit(1)

    # 讀 JSON 取週次資訊
    data = json.loads(json_path.read_text(encoding="utf-8"))
    period = data.get("report_period", {})
    iso_week = period.get("iso_week", "XX")
    week_start = period.get("week_start", "")
    week_end = period.get("week_end", "")

    week_label = f"W{iso_week:02d}_{week_start}_to_{week_end}" if isinstance(iso_week, int) else f"WXX_{week_start}_to_{week_end}"
    pdf_path = repo_root / "docs" / f"ALUXE_{market.upper()}_週報_{week_label}.pdf"

    # 1. PDF
    print("=" * 50)
    print(f"  ALUXE {market.upper()} PDF 補發")
    print("=" * 50)
    generate_pdf(market, json_path=json_path, pdf_path=pdf_path)

    # 2. Drive
    info = upload_pdf_to_drive(pdf_path, FOLDER_ID, SERVICE_ACCOUNT)
    drive_url = info.get("webViewLink", "")

    # 3. Telegram（簡短訊息，只附連結）
    if TG_TOKEN and TG_CHAT and drive_url:
        date = data.get("generated_at", "")[:10]
        msg = (
            f"📄 ALUXE {market.upper()} 競品市場週報（PDF 補發）· {date}\n\n"
            f"完整 PDF 報告：\n{drive_url}\n\n"
            f"（先前的 Telegram 摘要為同一份分析）"
        )
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": msg},
                timeout=30,
            ).raise_for_status()
            print("[Telegram] 補發通知已送出")
        except Exception as e:
            print(f"[Telegram] 補發失敗（不影響 PDF 已上 Drive）：{e}")

    print()
    print(f"✅ 完成")
    print(f"   PDF：{pdf_path.name}")
    print(f"   Drive：{drive_url}")


if __name__ == "__main__":
    main()
