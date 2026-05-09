@echo off
chcp 65001 >nul
echo [%DATE% %TIME%] Pausing backfill-prices...
curl -s -X POST http://localhost:8000/api/admin/jobs/backfill-prices/pause
echo.
