# Email 驗證碼註冊 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把現有「email + 密碼 + 手機 + 手機驗證碼」註冊流程、改成「email + 密碼 + email 驗證碼」流程。使用者填表單 → 收到 email 驗證碼 → 輸入驗證碼 → 才建帳號入 DB（先驗證才註冊、流程 A）。

**Architecture:** 用 SendGrid 寄信（free tier 100 封/天）。新建表 `email_verifications` 暫存「未驗證的註冊資料」（包含 email、密碼 hash、display_name、6 位驗證碼）、驗證通過才把資料 promote 到 `users` 表。沒設 `SENDGRID_API_KEY` 時退回 dev mode（print 驗證碼到 console、API 回傳給前端、不真寄信）。

**Tech Stack:**
- 後端：FastAPI / aiosqlite / SendGrid Python SDK
- 前端：原生 JS（單檔 SPA）
- 寄信：SendGrid REST API v3

---

## 動工前 user 自助設定（不可代辦、需在 Task 1 之前完成）

> SendGrid 帳號 + API key 必須 user 自己去申請、我拿不到。動工 Task 1 前先做：

1. 去 `sendgrid.com` 註冊免費帳號（個人免費 100 封/天）
2. 後台 → **Settings → Sender Authentication** → **Single Sender Verification** → 加一個寄信來源 email（如自己的 Gmail）、SendGrid 寄驗證信、點連結確認
3. 後台 → **Settings → API Keys** → **Create API Key** → 選 Full Access → 複製 key（**只顯示一次**、要先存好）
4. 把 2 個值貼到專案根目錄 `.env`：
   ```
   SENDGRID_API_KEY=SG.xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   SENDGRID_FROM_EMAIL=你驗證過的@gmail.com
   ```
5. 跟我說「設定好了」、我才開始 Task 1

> **沒設定也可以動工**：dev mode 會 print 驗證碼到 console、開發階段照樣能測。但要真寄信給使用者就必須設好。

---

## File Structure（會動到的檔案）

| 檔案 | 角色 | 建 / 改 |
|---|---|---|
| `app/email_sender.py` | SendGrid 寄信 module（含 dev fallback） | **新建** |
| `app/database.py` | 加 `email_verifications` 表進 init_db | 改（CREATE TABLE 區段、line 167-190 附近）|
| `app/main.py` | 加 2 個新端點 + deprecate 舊 `/register`（保留向後相容回 410）+ 拿掉 `/find-email` | 改（line 2181-2218 + 周邊）|
| `卡波/index.html` | 註冊 modal 改兩階段 UI + 拿掉手機欄位 | 改（line 874-918 表單 + line 3024+ JS handler）|
| `.env` | 加 SendGrid 變數 | 改（user 自己加） |
| `requirements.txt` | 加 `sendgrid` 套件 | 改（若有檔案的話）|
| `docs/sendgrid_setup.md` | SendGrid 申請步驟給 user / 未來新人 | **新建** |

---

## Task 1: 建寄信 module（SendGrid + dev fallback）

**Files:**
- Create: `app/email_sender.py`
- Install: `sendgrid` Python 套件
- 環境變數：`SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL`（user 自己加進 .env）

- [ ] **Step 1：裝 SendGrid 套件**

```powershell
./Python/bin/python.exe -m pip install sendgrid
```

預期：印「Successfully installed sendgrid-x.x.x」+ 相依套件。

- [ ] **Step 2：建 `app/email_sender.py`**

```python
"""寄 email 驗證碼（SendGrid）

環境變數：
- SENDGRID_API_KEY：SendGrid API key（沒設 → dev mode、print code 到 console）
- SENDGRID_FROM_EMAIL：寄信來源 email（已在 SendGrid 後台驗證過的）

dev mode：API_KEY 沒設時、不真寄信、把 code 印到 stdout 並回 True、保留 caller 流程不變
"""
import os
from typing import Optional


def _is_configured() -> bool:
    return bool(os.getenv("SENDGRID_API_KEY") and os.getenv("SENDGRID_FROM_EMAIL"))


def send_verification_code(to_email: str, code: str) -> bool:
    """寄 6 位 email 驗證碼到 to_email
    回 True 表示成功（或 dev mode print 成功）、False 表示寄失敗（API error）
    """
    if not _is_configured():
        print(f"[EMAIL/DEV] to={to_email} code={code} (SendGrid 未設定、走 dev mode)")
        return True

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except ImportError:
        print("[EMAIL/ERR] sendgrid 套件未安裝、退回 dev mode")
        print(f"[EMAIL/DEV] to={to_email} code={code}")
        return True

    subject = "卡波 - 您的註冊驗證碼"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;background:#f7f8fa;">
      <h2 style="color:#1a1a1a;">卡波 註冊驗證碼</h2>
      <p style="color:#555;">您正在註冊卡波（寶可夢卡 PSA 鑑定價格查詢）。驗證碼如下：</p>
      <div style="font-size:32px;font-weight:bold;color:#e63946;letter-spacing:6px;text-align:center;padding:24px;background:#fff;border-radius:8px;margin:16px 0;">
        {code}
      </div>
      <p style="color:#888;font-size:13px;">10 分鐘內有效。若非本人操作、請忽略此信。</p>
    </div>
    """
    message = Mail(
        from_email=os.environ["SENDGRID_FROM_EMAIL"],
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    try:
        sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
        resp = sg.send(message)
        ok = 200 <= resp.status_code < 300
        if not ok:
            print(f"[EMAIL/ERR] to={to_email} status={resp.status_code} body={resp.body}")
        return ok
    except Exception as e:
        print(f"[EMAIL/ERR] to={to_email} exception={e}")
        return False
```

- [ ] **Step 3：手動測試 dev mode（不設 API key 時）**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "from app.email_sender import send_verification_code; print(send_verification_code('test@example.com', '123456'))"
```

預期 stdout：
```
[EMAIL/DEV] to=test@example.com code=123456 (SendGrid 未設定、走 dev mode)
True
```

- [ ] **Step 4：手動測試真實 SendGrid（user 設好 .env 後）**

```powershell
# 先載入 .env（uvicorn 啟動時用 python-dotenv 已做、這裡手動）
$env:SENDGRID_API_KEY="SG.xxxx..."
$env:SENDGRID_FROM_EMAIL="your@verified.com"
$env:PYTHONIOENCODING="utf-8"
./Python/bin/python.exe -c "from app.email_sender import send_verification_code; print(send_verification_code('YOUR_TEST_EMAIL@gmail.com', '654321'))"
```

預期：印 `True`、收信信箱 1 分鐘內收到驗證碼信。若收不到 → 檢查 SendGrid 後台 Activity Feed 看 status。

- [ ] **Step 5：commit**

```powershell
git add app/email_sender.py
git commit -m "feat(auth): 加 SendGrid 寄信 module + dev mode fallback"
```

---

## Task 2: 新建 `email_verifications` 表 schema

**Files:**
- Modify: `app/database.py`（CREATE TABLE 區段、`phone_codes` 表附近、line 167-176 之後）

> 這張表暫存「使用者按了註冊但還沒輸入驗證碼」的資料。驗證通過才把資料搬到 `users` 表。

- [ ] **Step 1：在 `app/database.py` init_db() 內加 CREATE TABLE**

定位：找到 `CREATE TABLE IF NOT EXISTS phone_codes` 那段（line 168-176 附近）、緊接在後面加：

```python
    # Email 驗證碼（流程 A：先驗證才註冊、暫存註冊資料）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_verifications (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verifications_expires ON email_verifications(expires_at)")
```

**為什麼 email 當 PK**：同一個 email 只能有一筆暫存驗證碼。重發驗證碼 = UPSERT 覆蓋舊 row（更新 code + expires_at + 重置 attempts）。

**為什麼 password_hash 暫存在這**：流程 A 必須在驗證通過才寫進 users 表、密碼又不能用明文留 console。已 hash 過的密碼跟 users 表一樣安全度。

- [ ] **Step 2：跑 init_db 確認建表成功**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "from app.database import init_db; init_db(); import sqlite3; c=sqlite3.connect('cards.db'); print(c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='email_verifications'\").fetchall())"
```

預期：印 `[('email_verifications',)]`。

- [ ] **Step 3：commit**

```powershell
git add app/database.py
git commit -m "feat(auth): 加 email_verifications 表 schema（暫存未驗證註冊資料）"
```

---

## Task 3: 新端點 `POST /api/auth/register-request`（寄驗證碼）

**Files:**
- Modify: `app/main.py`（在現有 `/api/auth/register` line 2181 之前加新端點）

> 使用者填註冊表單 → 打這個端點 → 系統產驗證碼 + 暫存 + 寄信。

- [ ] **Step 1：在 `app/main.py` `/api/auth/register` 之前加新端點**

定位：line 2180 之前（在 `_verify_phone_code` 之後、`auth_register` 之前）插入：

```python
import string  # 確認檔案頂部已 import；沒的話加

# ===== Email 驗證碼產生 + 節流 =====

EMAIL_CODE_TTL_MIN = 10           # 驗證碼 10 分鐘過期
EMAIL_CODE_RESEND_COOLDOWN_SEC = 60  # 重發冷卻 60 秒
EMAIL_CODE_MAX_ATTEMPTS = 5       # 最多驗證 5 次

def _gen_email_code() -> str:
    """產 6 位數字驗證碼"""
    return "".join(_secrets.choice(string.digits) for _ in range(6))


@app.post("/api/auth/register-request")
async def auth_register_request(payload: dict = Body(...)):
    """流程 A 階段 1：填註冊表單 → 暫存資料 + 寄驗證碼到 email
    payload: {email, password, display_name?}
    回應：{ok: true, message, dev_code?}
    """
    from app.email_sender import send_verification_code as _send_code
    import aiosqlite

    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    display_name = (payload.get("display_name") or "").strip() or email.split("@")[0]

    # 1. 格式驗證
    if not auth_mod.EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="email 格式錯誤")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 字元")

    # 2. email 已註冊檢查
    async with aiosqlite.connect(DB_PATH) as db:
        if await (await db.execute("SELECT id FROM users WHERE email=?", (email,))).fetchone():
            raise HTTPException(status_code=409, detail="此 email 已註冊")

        # 3. 重發冷卻：同 email 60 秒內已寄過就擋
        cur = await db.execute(
            "SELECT created_at FROM email_verifications WHERE email=?", (email,)
        )
        row = await cur.fetchone()
        if row:
            try:
                last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - last).total_seconds() < EMAIL_CODE_RESEND_COOLDOWN_SEC:
                    raise HTTPException(status_code=429, detail=f"請等 {EMAIL_CODE_RESEND_COOLDOWN_SEC} 秒再重發")
            except ValueError:
                pass

        # 4. 產 code + hash 密碼 + UPSERT 暫存
        try:
            pw_hash = auth_mod.hash_password(password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        code = _gen_email_code()
        expires = (datetime.utcnow() + timedelta(minutes=EMAIL_CODE_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")

        await db.execute(
            """INSERT INTO email_verifications (email, code, password_hash, display_name, attempts, expires_at, created_at)
               VALUES (?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(email) DO UPDATE SET
                   code=excluded.code,
                   password_hash=excluded.password_hash,
                   display_name=excluded.display_name,
                   attempts=0,
                   expires_at=excluded.expires_at,
                   created_at=CURRENT_TIMESTAMP""",
            (email, code, pw_hash, display_name, expires),
        )
        await db.commit()

    # 5. 寄信
    ok = _send_code(email, code)
    if not ok:
        raise HTTPException(status_code=502, detail="寄信失敗、請稍後再試")

    out = {"ok": True, "message": f"驗證碼已寄到 {email}（10 分鐘內有效）"}
    if _is_dev_mode():
        out["dev_code"] = code
    return out
```

**注意點**：
- `auth_mod.EMAIL_RE` 已存在於 `app/auth.py:26`、直接 reuse
- `_secrets` 已 import 在 main.py 頂部（password_resets 用到）
- `_is_dev_mode()` 也已存在（forgot-password 用到）
- 對「此 email 已註冊」我們直接 raise 409 不像 forgot-password 隱藏存在性 — 因為 user 註冊時直接告知比較友善、且新用戶不會 brute-force 探測 email 是否註冊（價值低）

- [ ] **Step 2：重啟 API 並 curl 測試（dev mode）**

```powershell
# kill 舊 API
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
# 啟新版
./Python/bin/python.exe run_api.py
```

另開 terminal：

```powershell
curl -X POST http://localhost:8000/api/auth/register-request -H "Content-Type: application/json" -d '{\"email\":\"test_user_1@example.com\",\"password\":\"abc123\",\"display_name\":\"測試\"}'
```

預期：
```json
{"ok": true, "message": "驗證碼已寄到 test_user_1@example.com（10 分鐘內有效）", "dev_code": "123456"}
```
（dev_code 是隨機 6 位數）

console 印：`[EMAIL/DEV] to=test_user_1@example.com code=123456 (SendGrid 未設定、走 dev mode)`

DB 應有 row：
```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); print(c.execute('SELECT email, code, attempts FROM email_verifications').fetchall())"
```

- [ ] **Step 3：測試重發冷卻**

立刻再打同 endpoint 同 email：

```powershell
curl -X POST http://localhost:8000/api/auth/register-request -H "Content-Type: application/json" -d '{\"email\":\"test_user_1@example.com\",\"password\":\"abc123\"}'
```

預期 429：`{"detail":"請等 60 秒再重發"}`

- [ ] **Step 4：commit**

```powershell
git add app/main.py
git commit -m "feat(auth): 加 /api/auth/register-request 端點（流程 A 階段 1 寄驗證碼）"
```

---

## Task 4: 新端點 `POST /api/auth/register-verify`（比對 + 建帳號）

**Files:**
- Modify: `app/main.py`（緊接在 register-request 之後加）

- [ ] **Step 1：加新端點**

```python
@app.post("/api/auth/register-verify")
async def auth_register_verify(payload: dict = Body(...)):
    """流程 A 階段 2：使用者輸入驗證碼 → 建帳號 + 回 session token
    payload: {email, code}
    回應：{user, token}
    """
    import aiosqlite

    email = (payload.get("email") or "").strip().lower()
    code = (payload.get("code") or "").strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="缺少 email 或驗證碼")
    if not _re_phone.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="驗證碼格式錯誤（6 位數字）")

    async with aiosqlite.connect(DB_PATH) as db:
        # 1. 查暫存資料
        cur = await db.execute(
            "SELECT code, password_hash, display_name, attempts, expires_at FROM email_verifications WHERE email=?",
            (email,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="找不到此 email 的驗證紀錄、請重新註冊")
        stored_code, pw_hash, display_name, attempts, expires_at = row

        # 2. 超過嘗試次數
        if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="嘗試次數過多、請重新註冊")

        # 3. 過期
        try:
            exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() > exp:
                await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
                await db.commit()
                raise HTTPException(status_code=400, detail="驗證碼已過期、請重新註冊")
        except ValueError:
            raise HTTPException(status_code=400, detail="驗證資料異常、請重新註冊")

        # 4. attempt++
        await db.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE email=?", (email,))

        # 5. 比對 code
        if stored_code != code:
            await db.commit()
            raise HTTPException(status_code=400, detail="驗證碼錯誤")

        # 6. 通過 → 二次檢查 email 沒被搶建（race condition 防禦）
        if await (await db.execute("SELECT id FROM users WHERE email=?", (email,))).fetchone():
            await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
            await db.commit()
            raise HTTPException(status_code=409, detail="此 email 已被註冊")

        # 7. 建 user（直接 INSERT、不走 create_user 因為 password_hash 已預先 hash 好）
        cur = await db.execute(
            "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
            (email, display_name, pw_hash),
        )
        uid = cur.lastrowid

        # 8. 刪暫存
        await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
        await db.commit()

    # 9. 建 session
    user = await auth_mod.get_user_by_id(uid)
    token = await auth_mod.create_session(uid)
    return {"user": user, "token": token}
```

- [ ] **Step 2：重啟 API + 端到端 curl 測試**

```powershell
# 重啟 API
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py

# 階段 1：寄驗證碼
curl -X POST http://localhost:8000/api/auth/register-request -H "Content-Type: application/json" -d '{\"email\":\"flow_test@example.com\",\"password\":\"abc123\"}'
# 注意：response 裡 dev_code=XXXXXX
```

```powershell
# 階段 2：用 dev_code 驗證（替換 XXXXXX）
curl -X POST http://localhost:8000/api/auth/register-verify -H "Content-Type: application/json" -d '{\"email\":\"flow_test@example.com\",\"code\":\"XXXXXX\"}'
```

預期：`{"user": {...}, "token": "..."}`。

- [ ] **Step 3：測試錯誤分支**

a. 錯驗證碼：

```powershell
curl -X POST http://localhost:8000/api/auth/register-verify -H "Content-Type: application/json" -d '{\"email\":\"flow_test_2@example.com\",\"code\":\"000000\"}'
```
（先用 register-request 建好 flow_test_2 但故意輸入錯 code）

預期：`{"detail":"驗證碼錯誤"}`、attempts 增加。

b. 5 次連錯後：預期 `{"detail":"嘗試次數過多、請重新註冊"}`。

c. email 重複：

```powershell
# 對已註冊的 flow_test 再跑 register-request → 預期 409
curl -X POST http://localhost:8000/api/auth/register-request -H "Content-Type: application/json" -d '{\"email\":\"flow_test@example.com\",\"password\":\"abc123\"}'
```

預期：`{"detail":"此 email 已註冊"}`。

- [ ] **Step 4：DB 確認 user 已建好**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); print(c.execute('SELECT id, email, display_name FROM users WHERE email LIKE \"flow_test%\"').fetchall())"
```

預期：印出 1-2 個 flow_test 帳號 row。

- [ ] **Step 5：commit**

```powershell
git add app/main.py
git commit -m "feat(auth): 加 /api/auth/register-verify 端點（流程 A 階段 2 比對 + 建帳號）"
```

---

## Task 5: deprecate 舊端點（`/api/auth/register` + `/api/auth/find-email`）

**Files:**
- Modify: `app/main.py`（改現有 `/api/auth/register` line 2181-2218 + `/api/auth/find-email` line 2223-2247）

> 舊 `/api/auth/register` 用手機驗證、舊 `/api/auth/find-email` 用手機查 email — 現在拿掉手機驗證、這兩個都不需要。為了向後相容、不直接刪除而是回 410 Gone。

- [ ] **Step 1：替換 `auth_register` 函式 body 為 410**

定位 `app/main.py` line 2181 `@app.post("/api/auth/register")`、整個 `auth_register` 函式 body 替換成：

```python
@app.post("/api/auth/register")
async def auth_register(payload: dict = Body(...)):
    """[DEPRECATED 2026-05-22] 舊「手機驗證碼」註冊已淘汰、改用 email 驗證碼兩階段流程：
    - POST /api/auth/register-request：寄驗證碼到 email
    - POST /api/auth/register-verify：輸入驗證碼 + 建帳號
    """
    raise HTTPException(
        status_code=410,
        detail="此端點已淘汰、請改用 /api/auth/register-request + /api/auth/register-verify",
    )
```

- [ ] **Step 2：替換 `auth_find_email` 函式 body 為 410**

定位 `app/main.py` line 2223 `@app.post("/api/auth/find-email")`、整個函式 body 替換成：

```python
@app.post("/api/auth/find-email")
async def auth_find_email(payload: dict = Body(...)):
    """[DEPRECATED 2026-05-22] 現在不再用手機註冊、email 直接就是登入帳號、不需要此端點"""
    raise HTTPException(
        status_code=410,
        detail="此端點已淘汰、email 就是登入帳號、無需手機找回",
    )
```

- [ ] **Step 3：重啟 API + 測試舊端點回 410**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

```powershell
curl -X POST http://localhost:8000/api/auth/register -H "Content-Type: application/json" -d '{\"email\":\"x@y.z\"}'
```

預期 410 `{"detail":"此端點已淘汰、請改用 /api/auth/register-request + /api/auth/register-verify"}`。

- [ ] **Step 4：commit**

```powershell
git add app/main.py
git commit -m "refactor(auth): deprecate /api/auth/register + /find-email（回 410、改用 email 兩階段流程）"
```

---

## Task 6: 前端註冊 modal 兩階段 UI

**Files:**
- Modify: `C:\Users\Dong Ying\Desktop\卡波\index.html`（line 874-918 表單 + line 3024-3110 JS）

> 註冊 tab 改成兩階段：先收 email + 密碼 + display_name → 按「寄驗證碼」→ 切換到階段 2 顯示驗證碼輸入框 → 按「驗證並完成註冊」。同步拿掉手機 + 手機驗證碼欄位。

**設計重點**：
- 新增 `state.regStage` 變數：`'form'`（階段 1）或 `'code'`（階段 2）
- 階段 1 顯示：email + display_name + 密碼 + 密碼確認 + 「寄驗證碼」按鈕
- 階段 2 顯示：「驗證碼已寄到 xxx@xxx」+ 6 位驗證碼輸入框 + 「驗證並完成註冊」按鈕 + 「重寄（60s）」按鈕 + 「修改 email」連結回階段 1
- 切換 login tab 時 reset 回階段 1

- [ ] **Step 1：先 Read 看現有 modal HTML 結構**

```
Read 卡波\index.html line 870-925
Read 卡波\index.html line 3020-3120
```

- [ ] **Step 2：改 HTML 表單區塊**

定位 line 874-918 附近。把現有「電話 / 手機驗證碼」欄位拿掉、加「驗證碼階段」區塊。預期最後結構：

```html
<!-- 階段 1：填表單 -->
<div id="authStage1">
  <input id="authEmail" type="email" placeholder="email" />
  <input id="authDisplayName" type="text" placeholder="顯示名稱（選填）" style="display:none" />  <!-- 只 register 顯示 -->
  <input id="authPassword" type="password" placeholder="密碼（至少 6 字元）" />
  <input id="authPassword2" type="password" placeholder="再次輸入密碼" style="display:none" />  <!-- 只 register 顯示 -->
  <button id="authSubmit" onclick="submitAuth()">登入</button>
</div>

<!-- 階段 2：輸入驗證碼（只在 register + 階段切換到 'code' 時顯示） -->
<div id="authStage2" style="display:none">
  <div id="authStage2Hint" style="font-size:13px;color:var(--c-text-2)">驗證碼已寄到 <span id="authStage2Email"></span>（10 分鐘內有效）</div>
  <input id="authCode" type="text" inputmode="numeric" maxlength="6" placeholder="6 位驗證碼" />
  <button onclick="submitAuthVerify()">驗證並完成註冊</button>
  <button id="authResendBtn" onclick="resendAuthCode()">重寄驗證碼</button>
  <a href="javascript:void(0)" onclick="backToAuthStage1()" style="font-size:12px;color:var(--c-text-2)">修改 email</a>
</div>
```

> **施工注意**：要對齊現有 CSS class（`grade-tab` / `btn-acc` 等）跟現有結構、不要硬塞新樣式。建議：先 Read 那塊 HTML、看現有 input class + button style、複用同樣 markup pattern。

- [ ] **Step 3：改 JS handler**

新增 4 個 function、改 1 個：

```javascript
// 新增 state 欄位
state.regStage = 'form';    // 'form' | 'code'
state.pendingEmail = '';    // 階段 2 顯示用 + verify 用
state.resendCooldownT = 0;  // 重寄倒數計時 id

// 改 switchAuthTab：reset 回階段 1
function switchAuthTab(mode) {
  state.authMode = mode;
  state.regStage = 'form';  // 新增
  document.getElementById('authStage1').style.display = '';
  document.getElementById('authStage2').style.display = 'none';
  // ... 其餘維持
}

// 改 submitAuth：register 模式時打 register-request 而非舊 register
async function submitAuth() {
  const email = document.getElementById('authEmail').value.trim();
  const password = document.getElementById('authPassword').value;
  if (state.authMode === 'login') {
    // 既有 login 邏輯維持
    const r = await api('/api/auth/login', null, {method:'POST', body:{email, password}});
    state.token = r.token; state.user = r.user; closeAuth(); refreshNav(); toast('登入成功');
    return;
  }
  // register
  const password2 = document.getElementById('authPassword2').value;
  if (password !== password2) { toast('兩次密碼不一致'); return; }
  if (password.length < 6) { toast('密碼至少 6 字元'); return; }
  const display_name = document.getElementById('authDisplayName').value.trim();
  try {
    const r = await api('/api/auth/register-request', null, {method:'POST', body:{email, password, display_name}});
    state.pendingEmail = email;
    state.regStage = 'code';
    document.getElementById('authStage1').style.display = 'none';
    document.getElementById('authStage2').style.display = '';
    document.getElementById('authStage2Email').textContent = email;
    document.getElementById('authCode').value = '';
    startResendCooldown();
    toast('驗證碼已寄出');
    if (r.dev_code) {
      // dev mode：把 code 自動填進輸入框、方便測試
      document.getElementById('authCode').value = r.dev_code;
      console.log('[DEV] code=' + r.dev_code);
    }
  } catch (e) {
    toast(e.detail || '註冊失敗');
  }
}

async function submitAuthVerify() {
  const code = document.getElementById('authCode').value.trim();
  if (!/^\d{6}$/.test(code)) { toast('驗證碼格式錯誤（6 位數字）'); return; }
  try {
    const r = await api('/api/auth/register-verify', null, {method:'POST', body:{email: state.pendingEmail, code}});
    state.token = r.token; state.user = r.user;
    closeAuth(); refreshNav();
    toast('註冊成功、已自動登入');
  } catch (e) {
    toast(e.detail || '驗證失敗');
  }
}

async function resendAuthCode() {
  const btn = document.getElementById('authResendBtn');
  if (btn.disabled) return;
  // 用既有的 register-request 端點重發、後端有 60s cooldown 擋
  const password = document.getElementById('authPassword').value;
  const display_name = document.getElementById('authDisplayName').value.trim();
  try {
    const r = await api('/api/auth/register-request', null, {method:'POST', body:{email: state.pendingEmail, password, display_name}});
    startResendCooldown();
    toast('驗證碼已重寄');
    if (r.dev_code) document.getElementById('authCode').value = r.dev_code;
  } catch (e) {
    toast(e.detail || '重寄失敗');
  }
}

function backToAuthStage1() {
  state.regStage = 'form';
  document.getElementById('authStage1').style.display = '';
  document.getElementById('authStage2').style.display = 'none';
  clearInterval(state.resendCooldownT);
}

function startResendCooldown() {
  const btn = document.getElementById('authResendBtn');
  let sec = 60;
  clearInterval(state.resendCooldownT);
  btn.disabled = true;
  btn.textContent = `重寄（${sec}s）`;
  state.resendCooldownT = setInterval(() => {
    sec--;
    if (sec <= 0) {
      clearInterval(state.resendCooldownT);
      btn.disabled = false;
      btn.textContent = '重寄驗證碼';
    } else {
      btn.textContent = `重寄（${sec}s）`;
    }
  }, 1000);
}
```

- [ ] **Step 4：瀏覽器手動測試**

開 `http://localhost:8080/index.html` → 按「登入 / 註冊」→ 切到「註冊」tab → 填 email + display_name + 密碼 + 密碼確認 → 按「寄驗證碼」

預期：
1. modal 切到階段 2、顯示「驗證碼已寄到 xxx@xxx」
2. 「重寄驗證碼」按鈕顯示「重寄（60s）」並倒數
3. dev mode：驗證碼自動填進輸入框
4. 按「驗證並完成註冊」→ toast「註冊成功」+ modal 關閉 + nav 變成「歡迎 xxx」

也測：
- 兩次密碼不一致 → toast「兩次密碼不一致」
- 修改 email → 回階段 1
- 60s 內按重寄 → toast「請等 60 秒再重發」

- [ ] **Step 5：commit**

```powershell
git add "../卡波/index.html"
git commit -m "feat(ui): 註冊 modal 改 email 驗證碼兩階段 + 拿掉手機欄位"
```

> **注意**：`卡波\index.html` 不在這個 git repo 裡（姊妹目錄）。要 commit 該檔須切到該目錄獨立 git management、或寫進專案 README 提醒。Step 5 commit 命令可能 fail、若 fail 跳過 commit、手動備份檔案到 `卡波\index.html.before-email-verify`。

---

## Task 7: SendGrid 設定文件

**Files:**
- Create: `docs/sendgrid_setup.md`

- [ ] **Step 1：建 setup 文件**

```markdown
# SendGrid 寄信設定步驟

## 為什麼要這個

卡波 email 驗證碼註冊功能需要寄信。我們用 SendGrid（免費 100 封/天）。

## 申請步驟（首次）

1. 到 https://sendgrid.com 註冊免費帳號
2. **驗證寄信來源**：
   - 後台 → Settings → Sender Authentication
   - 點 **Single Sender Verification** → 加一個寄信 email（建議用 Gmail 或自己擁有的 domain）
   - SendGrid 寄驗證信、點連結確認
3. **建 API key**：
   - 後台 → Settings → API Keys
   - 點 **Create API Key** → 選 **Full Access** → 複製 key
   - **注意：key 只顯示一次、要先存好**
4. **設定 .env**：

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

`.env` 沒設這兩個變數時、系統自動走 dev mode：驗證碼會 print 到 console、不真寄信、API 回傳 dev_code 給前端、開發測試夠用。

## 確認生效

收信信箱應在 1 分鐘內收到驗證碼信。若收不到：
- 檢查垃圾信件夾
- SendGrid 後台 → Activity Feed 看 status（看 bounce / blocked / dropped 原因）
- 確認 SENDGRID_FROM_EMAIL 跟你在 Sender Authentication 驗證過的 email 一致

## 額度

免費版每天 100 封、永久免費。MVP 階段夠用。要擴充：
- Essentials $19.95/月 = 50,000 封
- Pro $89.95/月 = 100,000 封 + 多 sender
```

- [ ] **Step 2：commit**

```powershell
git add docs/sendgrid_setup.md
git commit -m "docs: SendGrid 設定步驟說明"
```

---

## Task 8: 端到端真實 email 測試（SendGrid 設定好之後）

**Files:** （無 code 改動、純驗收）

> 這個 task 只在 user 已經完成 Task 0「user 自助設定」之後跑、確認真的能收到 email。

- [ ] **Step 1：確認 .env 有 SENDGRID_API_KEY + SENDGRID_FROM_EMAIL**

```powershell
findstr SENDGRID .env
```

預期：印出兩行。

- [ ] **Step 2：重啟 API 載入新 env**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

- [ ] **Step 3：用真實 email 跑流程**

開瀏覽器 → 註冊 modal → 填**真實的 email**（建議自己的 Gmail）→ 寄驗證碼 → 確認真實信箱 1 分鐘內收到 → 輸入驗證碼 → 完成註冊。

- [ ] **Step 4：DB 確認 user 真的建好**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); print(c.execute('SELECT id, email, display_name, created_at FROM users ORDER BY id DESC LIMIT 5').fetchall())"
```

預期：最新 row 是剛註冊的真實 email。

- [ ] **Step 5（選做）：清測試帳號**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); c.execute('DELETE FROM users WHERE email LIKE \"%test%\" OR email LIKE \"flow_test%\"'); c.commit(); print('cleaned')"
```

---

## 驗收檢查清單

- [ ] 註冊 modal 階段 1 / 階段 2 切換流暢
- [ ] 寄真實 email + 真實信箱收到
- [ ] 驗證碼錯誤、過期、超過嘗試次數都有正確錯誤訊息
- [ ] 重發冷卻 60s 倒數正常
- [ ] DB users 表新 row 含正確 email / display_name / created_at
- [ ] 登入 endpoint 用新 user 能 login
- [ ] 舊 `/api/auth/register` + `/find-email` 回 410
- [ ] dev mode（沒設 SENDGRID_API_KEY）也能跑、code 出現在 console + response

---

## 預估工時

| Task | 工時 | 備註 |
|---|---|---|
| Task 1 寄信 module | 30-45 min | 含 dev / 真寄各測一次 |
| Task 2 schema | 10 min | 一張表、PRAGMA 確認 |
| Task 3 register-request | 30-45 min | 含 cooldown 測試 |
| Task 4 register-verify | 30-45 min | 含錯誤分支測試 |
| Task 5 deprecate 舊端點 | 10 min | 兩個函式 body 替換 |
| Task 6 前端兩階段 UI | 60-90 min | 含瀏覽器手動測 |
| Task 7 setup 文件 | 15 min | |
| Task 8 真實 email 驗收 | 15 min | 跑一次 |
| **總計** | **3-5 hr** | 不含 user 自己申請 SendGrid 帳號 |

---

## 風險 / 注意點

1. **`卡波\index.html` 不在這個 repo**：前端改動可能無法直接 git commit、要在該目錄獨立 backup（`index.html.before-email-verify`）或手動建獨立 git
2. **既有 user 表已有 row 可能含「沒對應 email_verifications」的舊測試帳號**：不影響、新流程新 user 不會撞舊資料
3. **race condition**：同 email 兩個瀏覽器同時走 register-verify → 一個建成 user、另一個會撞「此 email 已被註冊」回 409、沒 corrupt 風險
4. **SendGrid API key 安全**：寫進 `.env`、`.env` 已在 `.gitignore` 中（檢查確認）、不會被 commit
5. **dev mode 安全**：dev_code 回傳前端 + console 顯示、正式環境（`CARDPOOL_DEV_MODE` 沒設）絕對不可開、否則任何人都能拿到別人的 code
6. **舊 sms 流程的 `phone_codes` 表**：保留不刪、未來 KYC / 賣家驗證可能會用、schema 已存在無成本

---

## Self-Review

- [x] **Spec coverage**：流程 A（先驗證才註冊）✓、SendGrid ✓、拿掉手機 ✓、6 位驗證碼 ✓、10 min 過期 ✓、60s 重發 cooldown ✓、5 次嘗試 lock ✓、dev mode fallback ✓
- [x] **Placeholder scan**：所有 code 都是完整可貼上的 snippet、沒 TODO / TBD
- [x] **Type consistency**：`EMAIL_CODE_TTL_MIN` / `EMAIL_CODE_RESEND_COOLDOWN_SEC` / `EMAIL_CODE_MAX_ATTEMPTS` 命名 task 3-4 一致；`state.pendingEmail` / `state.regStage` 命名前端 task 6 一致
