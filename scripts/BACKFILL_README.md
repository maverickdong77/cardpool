# 缺價卡片 Backfill — 操作說明

## 三種使用方式

### 方式一：網頁管理 UI（最簡單）

打開 http://localhost:8000/admin/jobs ，找到 `backfill-prices` 那一列：

- **▶ 啟動** — 開始跑 backfill
- **⏸ 暫停** — 暫停（當前 batch 跑完才停，最多等 ~3 分鐘）
- **▶ 恢復** — 從暫停狀態繼續
- **■ 停止** — 整個結束（要重啟才能再跑）

進度條會即時顯示：已完成幾張 / 總共幾張 / ETA / 速率。

### 方式二：點兩下 .bat 檔

在 `scripts/` 資料夾裡：
- `start_backfill.bat` — 啟動
- `pause_backfill.bat` — 暫停
- `resume_backfill.bat` — 恢復
- `stop_backfill.bat` — 停止
- `status_backfill.bat` — 看狀態

### 方式三：命令列（快）

```cmd
curl -X POST http://localhost:8000/api/admin/jobs/backfill-prices/start
curl -X POST http://localhost:8000/api/admin/jobs/backfill-prices/pause
curl -X POST http://localhost:8000/api/admin/jobs/backfill-prices/resume
curl -X POST http://localhost:8000/api/admin/jobs/backfill-prices/stop
```

---

## 改參數（並行 / batch / 語言）

```cmd
REM 改成只跑 jp 卡，並行 5
curl -X POST http://localhost:8000/api/admin/backfill/settings ^
  -H "Content-Type: application/json" ^
  -d "{\"lang\":\"jp\",\"concurrency\":5}"
```

可改 keys：
- `lang` — `"jp"` / `"en"` / `"all"`（預設 all）
- `concurrency` — 並行 worker 數（預設 3，每個 ~250 MB chromium，5 以上電腦會卡）
- `batch_size` — 每批幾張卡（預設 200）
- `cooldown` — 每 batch 間休息秒數（預設 60）
- `max_batches` — 跑 N 個 batch 就停（預設 null = 無限）

**設定要在 job idle 狀態下改，跑起來才生效。**

---

## Windows Task Scheduler 自動排程

若要每天凌晨 3 點自動跑 backfill 直到完成：

1. 開「工作排程器」(taskschd.msc)
2. 右側「建立工作」
3. **一般** 分頁：
   - 名稱：`Cardpool 缺價 backfill`
   - 勾「不論使用者登入」
4. **觸發程序** 分頁 → 新增：
   - 設定每日 03:00
5. **動作** 分頁 → 新增：
   - 動作：啟動程式
   - 程式：`C:\Users\Dong Ying\Desktop\Cardpool Price Searching\scripts\start_backfill.bat`
6. **條件** 分頁：
   - ☐ 取消勾「以電池運作時不要啟動」
   - ☑ 勾「只有當電腦處於閒置狀態」（可選）
7. **設定** 分頁：
   - ☑ 「如果工作執行超過」設成 12 小時 → 強制停止

每天會自動觸發。完成後 job 狀態會變成 `finished`，下次再排程或手動重啟即可。

---

## 使用流程建議

**第一次跑（全 backfill）**
1. 確認 cardpool 有開（`http://localhost:8000/api/health`）
2. 點 `start_backfill.bat`
3. 開瀏覽器看 http://localhost:8000/admin/jobs
4. 預估 10-12 小時完成 26k 卡（其中只有 1-2k 真的能抓到資料，因 SNKR/eBay 對冷門卡無紀錄）
5. 中途想暫停 → 點 `pause_backfill.bat`
6. 想看進度 → 點 `status_backfill.bat`

**之後固定維護**
- Task Scheduler 每天凌晨自動跑
- 跑完只會處理新增的缺價卡片（已有資料的會跳過）

---

## 中止 chromium 釋放 RAM

如果 backfill 跑太久電腦變卡，先暫停 job 再關 chromium：

```cmd
curl -X POST http://localhost:8000/api/admin/jobs/backfill-prices/pause
curl -X POST http://localhost:8000/api/admin/jobs/close-browsers
```

Resume 時 chromium 會自動重開。
