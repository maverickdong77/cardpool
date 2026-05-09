@echo off
chcp 65001 >nul
curl -s http://localhost:8000/api/admin/jobs ^| findstr /C:"backfill-prices" /C:"status" /C:"activity" /C:"done" /C:"with_data" /C:"percent" /C:"eta"
echo.
