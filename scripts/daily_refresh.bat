@echo off
chcp 65001 >nul
REM Daily refresh: SNKR + PriceCharting + PSA APR + promote ebay
REM Usage: scripts\daily_refresh.bat
REM Schedule: Task Scheduler at 02:00 daily
setlocal

set ROOT=C:\Users\Dong Ying\Desktop\Cardpool Price Searching
set PY=%ROOT%\Python\bin\python.exe
set LOG=%ROOT%\daily_refresh.log
set PYTHONIOENCODING=utf-8

cd /d "%ROOT%"

echo. >> "%LOG%"
echo ============================================ >> "%LOG%"
echo [%DATE% %TIME%] daily refresh start >> "%LOG%"
echo ============================================ >> "%LOG%"

echo [%DATE% %TIME%] Step 1/4 SNKR full history >> "%LOG%"
"%PY%" backfill_snkr_full_history.py --concurrency=12 --max-pages=10 --batch-size=200 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Step 1 done >> "%LOG%"

echo [%DATE% %TIME%] Step 2/4 PriceCharting all mapped >> "%LOG%"
"%PY%" backfill_pricecharting.py --concurrency=5 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Step 2 done >> "%LOG%"

echo [%DATE% %TIME%] Step 3/4 PSA APR all mapped >> "%LOG%"
"%PY%" backfill_psa_apr.py --ps=200 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Step 3 done >> "%LOG%"

echo [%DATE% %TIME%] Step 4/4 Promote eBay listings >> "%LOG%"
"%PY%" promote_ebay_listings.py >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Step 4 done >> "%LOG%"

echo [%DATE% %TIME%] daily refresh COMPLETE >> "%LOG%"

endlocal
