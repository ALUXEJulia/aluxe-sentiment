# ALUXE SG Sentiment Monitor — v2

每週自動抓取 SG 市場評論 → Claude AI 分析 → 三個地方同步輸出：

| 輸出 | 用途 |
|---|---|
| GitHub Pages | 視覺化儀表板，手機/電腦都能開 |
| Google Sheets | 歷史趨勢數據、跨週比較、可篩選 |
| Telegram Bot | 週報摘要推送 + 競品負評即時預警 |

---

## 設定步驟（約 25 分鐘）

### Step 1 — Push repo 到 GitHub

```bash
git init && git add .
git commit -m "init: aluxe sentiment v2"
git remote add origin https://github.com/[帳號]/aluxe-sentiment.git
git push -u origin main
```

### Step 2 — 建立 Google Sheets

1. 新建一個 Google Sheet，命名「ALUXE SG Sentiment」
2. 手動建立以下五個工作表（分頁名稱要完全一致）：

| 工作表名稱 | 用途 |
|---|---|
| `Dashboard` | 每週覆寫，顯示最新狀態 |
| `Weekly History` | 每週追加，完整歷史趨勢 |
| `Competitor Alerts` | 競品負評預警記錄 |
| `Hot Topics` | 熱門議題追蹤 |
| `Action Log` | 每週行動建議存檔 |

3. 複製網址中的 Sheet ID（`/d/` 和 `/edit` 之間那段）

### Step 3 — 建立 Google Service Account

1. 前往 [console.cloud.google.com](https://console.cloud.google.com)
2. 啟用 **Google Sheets API**（搜尋 API & Services → Enable）
3. IAM & Admin → Service Accounts → 新建 → 下載 JSON 金鑰
4. 把 Service Account 的 email 加為 Google Sheet 的**編輯者**（Sheet 右上角分享）

### Step 4 — 設定 GitHub Secrets

repo → Settings → Secrets and variables → Actions → New repository secret

| Secret 名稱 | 說明 |
|---|---|
| `APIFY_TOKEN` | Apify Console → Settings → Integrations |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `TELEGRAM_BOT_TOKEN` | 跟 @BotFather 說 `/newbot` |
| `TELEGRAM_CHAT_ID` | 傳訊給 Bot 後訪問 `api.telegram.org/bot[TOKEN]/getUpdates` |
| `GOOGLE_SHEETS_ID` | Sheet URL 中間那段 ID |
| `GOOGLE_SERVICE_ACCOUNT` | Service Account JSON 檔案的**完整內容**（整個貼入） |
| `GITHUB_PAGES_URL` | 你的 Pages 網址（選填） |

### Step 5 — 開啟 GitHub Pages

repo → Settings → Pages → Source: `main`，Folder: `/docs` → Save

### Step 6 — 手動跑第一次測試

repo → Actions → ALUXE SG Weekly Sentiment Report → Run workflow

---

## 排程

每週一 09:00 SGT 自動執行。需要即時跑：Actions → Run workflow。

## 費用估算（月）

| 工具 | 費用 |
|---|---|
| GitHub Actions + Pages | 免費 |
| Google Sheets | 免費 |
| Telegram Bot | 免費 |
| Apify | ~$20–49 USD |
| Claude API | ~$1–3 USD |

## 本機測試

```bash
export APIFY_TOKEN="apify_api_xxxx"
export ANTHROPIC_API_KEY="sk-ant-xxxx"
export TELEGRAM_BOT_TOKEN="xxxx:xxxx"
export TELEGRAM_CHAT_ID="xxxx"
export GOOGLE_SHEETS_ID="xxxx"
export GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'

pip install requests cryptography
python scripts/run_analysis_sg.py
```
