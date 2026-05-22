# SendGrid 寄信設定步驟

## 為什麼要這個

卡波 email 驗證碼註冊功能需要寄信。我們用 SendGrid（免費 100 封 / 天、永久免費）。

## 申請步驟（首次）

1. 到 https://sendgrid.com 註冊免費帳號（Twilio SendGrid Free Plan）

2. **驗證寄信來源 email**：
   - 後台 → **Settings** → **Sender Authentication**
   - 點 **Single Sender Verification** → **Create New Sender** → 填一個寄信 email（建議自己的 Gmail）
   - SendGrid 寄驗證信、點連結確認

3. **建 API key**：
   - 後台 → **Settings** → **API Keys** → **Create API Key**
   - 名稱隨意（如 `cardpool-dev`）、permission 選 **Full Access**
   - 複製 key 開頭 `SG.xxxxxxxx...`
   - **注意：key 只顯示一次、要先存好**

4. **設定 .env**：

   專案根目錄的 `.env` 加兩行：

   ```
   SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   SENDGRID_FROM_EMAIL=你驗證過的@gmail.com
   ```

5. **重啟 API**：

   ```powershell
   $pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
   ./Python/bin/python.exe run_api.py
   ```

## 沒設定也能跑（dev mode）

`.env` 沒設這兩個變數時、系統自動走 dev mode：

- 驗證碼會 print 到 console（API stdout）
- **不真寄信**
- API 回傳 `dev_code` 給前端、前端 toast 顯示
- 開發測試夠用

判斷在哪：看 console 出現 `[EMAIL/DEV] to=xxx code=123456` 就是 dev mode；出現 `[EMAIL/ERR]` 就是 SendGrid 有設定但寄信失敗。

## 確認真實寄信生效

設定好 `.env` + 重啟後、註冊一個真實 email、收信信箱應在 1 分鐘內收到驗證碼信。

若收不到：

1. **檢查垃圾信件夾**（首次寄常被分類成垃圾信）
2. SendGrid 後台 → **Activity** → **Activity Feed**：看 status
   - **Delivered**：成功送達（檢查垃圾匣）
   - **Bounce**：對方信箱不存在或拒收
   - **Blocked**：對方伺服器擋下（少見）
   - **Dropped**：SendGrid 主動丟掉（多為 spam 黑名單）
3. 確認 `SENDGRID_FROM_EMAIL` 跟你在 **Sender Authentication** 驗證過的 email 一致
4. API stdout 看有沒有 `[EMAIL/ERR]` 開頭的錯誤訊息

## 額度

| 方案 | 額度 | 月費 |
|---|---|---|
| Free | 100 封 / 天 | $0 永久 |
| Essentials | 50,000 封 / 月 | $19.95 |
| Pro | 100,000 封 / 月 + 多 sender | $89.95 |

MVP 階段 Free 夠用。上線後若用戶量上來、再升級。

## 相關檔案

- `app/email_sender.py`：寄信 module（含 dev mode fallback）
- `app/main.py`：`/api/auth/register-request` 端點呼叫寄信
- `.env`：放 API key（已在 `.gitignore` 中、不會被 commit）
