@echo off
REM 手動啟動 backfill-prices job
REM 用法：直接點兩下 / 或 Task Scheduler 排程觸發
REM 自動偵測 cardpool 是否在跑，沒跑就先把它叫起來
chcp 65001 >nul
setlocal

REM Step 1：檢查 cardpool API 是否在線
echo [%DATE% %TIME%] Checking cardpool API...
curl -s --max-time 3 -o NUL -w "%%{http_code}" http://localhost:8000/api/health > "%TEMP%\cp_health.txt"
set /p CP_HEALTH=<"%TEMP%\cp_health.txt"
del "%TEMP%\cp_health.txt" 2>NUL

if not "%CP_HEALTH%"=="200" (
  echo [%DATE% %TIME%] Cardpool not running, starting it in background...
  cd /d "C:\Users\Dong Ying\Desktop\Cardpool Price Searching"
  set CARDPOOL_DISABLE_JOBS=1
  start "Cardpool API" /B "C:\Users\Dong Ying\Desktop\Cardpool Price Searching\Python\bin\python.exe" -c "import uvicorn; uvicorn.run('app.main:app', port=8000, reload=False)" > "C:\Users\Dong Ying\Desktop\Cardpool Price Searching\cardpool_server.log" 2>&1
  echo [%DATE% %TIME%] Waiting 12 sec for cardpool to start...
  timeout /t 12 /nobreak >NUL
)

REM Step 2：觸發 backfill job
echo [%DATE% %TIME%] Triggering backfill-prices...
curl -s -X POST http://localhost:8000/api/admin/jobs/backfill-prices/start
echo.
echo [%DATE% %TIME%] Done. Status: http://localhost:8000/admin/jobs

endlocal
