# Cardpool Price Searching

Pokemon PSA 鑑定卡價格查詢服務，支援 eBay 與 SNKRDUNK 成交價格查詢。

## 功能

- PSA 編號查詢
- 卡片名稱搜尋
- eBay 成交價格
- SNKRDUNK 價格
- LINE 機器人查詢
- 價格歷史紀錄

## 專案結構

```
Cardpool Price Searching/
├── app/
│   ├── main.py          # FastAPI 主程式
│   ├── database.py      # 資料庫操作
│   ├── line_bot.py      # LINE 機器人處理
│   └── scraper/
│       ├── ebay.py      # eBay 爬蟲
│       └── snkrdunk.py  # SNKRDUNK 爬蟲
├── static/
│   └── index.html       # Landing Page
├── cards.db             # SQLite 資料庫（自動產生）
├── .env                 # 環境變數（需自行建立）
├── .env.example         # 環境變數範例
├── requirements.txt     # Python 套件
└── README.md
```

## 本地開發

### 1. 安裝套件

```bash
cd "Cardpool Price Searching"
pip install -r requirements.txt
```

### 2. 設定環境變數

複製 `.env.example` 為 `.env`，並填入你的 LINE 設定：

```bash
cp .env.example .env
```

編輯 `.env`：

```
LINE_CHANNEL_ACCESS_TOKEN=你的_Channel_Access_Token
LINE_CHANNEL_SECRET=你的_Channel_Secret
```

### 3. 取得 LINE 設定

1. 前往 [LINE Developers Console](https://developers.line.biz/console/)
2. 建立或選擇 Provider
3. 建立 Messaging API Channel
4. 在 "Messaging API" 分頁：
   - 點擊 "Issue" 取得 Channel Access Token
5. 在 "Basic settings" 分頁：
   - 複製 Channel Secret

### 4. 啟動服務

```bash
python -m app.main
```

或使用 uvicorn：

```bash
uvicorn app.main:app --reload --port 8000
```

服務啟動後：
- 網頁：http://localhost:8000
- API 文件：http://localhost:8000/docs

## 部署到 Railway

### 1. 準備檔案

建立 `Procfile`：

```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

建立 `runtime.txt`：

```
python-3.11.0
```

### 2. 部署步驟

1. 前往 [Railway](https://railway.app/)
2. 連結 GitHub 或直接上傳
3. 新增專案 → Deploy from GitHub repo
4. 設定環境變數：
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_CHANNEL_SECRET`
5. 部署完成後取得網址

### 3. 設定 LINE Webhook

1. 回到 LINE Developers Console
2. 在 "Messaging API" 分頁
3. 設定 Webhook URL：`https://你的網址.railway.app/webhook`
4. 開啟 "Use webhook"
5. 關閉 "Auto-reply messages"

## 部署到 Render

### 1. 建立 `render.yaml`

```yaml
services:
  - type: web
    name: cardpool
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: LINE_CHANNEL_ACCESS_TOKEN
        sync: false
      - key: LINE_CHANNEL_SECRET
        sync: false
```

### 2. 部署

1. 前往 [Render](https://render.com/)
2. New → Web Service
3. 連結 GitHub repo
4. 設定環境變數
5. Deploy

## API 端點

| 端點 | 方法 | 說明 |
|------|------|------|
| `/` | GET | Landing Page |
| `/webhook` | POST | LINE Webhook |
| `/api/health` | GET | 健康檢查 |
| `/api/search/psa/{cert}` | GET | PSA 編號查詢 |
| `/api/search/name/{name}` | GET | 卡片名稱查詢 |
| `/api/card/{cert}` | GET | 卡片詳細資訊 |
| `/api/card/{cert}/history` | GET | 價格歷史 |

## LINE 機器人使用

加入好友後：

- 輸入 **PSA 編號**（如 `12345678`）查詢精確價格
- 輸入 **卡片名稱**（如 `Pikachu VMAX`）搜尋相關卡片
- 輸入 `/help` 查看使用說明

## 注意事項

- eBay 和 SNKRDUNK 爬蟲可能因網站結構變動而失效
- 價格資料僅供參考，不代表實際市場價值
- 請遵守各網站的使用條款

## 授權

MIT License
