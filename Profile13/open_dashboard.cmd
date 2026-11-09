@echo off
setlocal
start "" "C:\Users\sandu\AppData\Local\Programs\Python\Python312\pythonw.exe" "C:\ChromePermanentHistory\Profile13\cloud_sync.py" --daemon
start "" "C:\Users\sandu\AppData\Local\Programs\Python\Python312\pythonw.exe" "C:\ChromePermanentHistory\Profile13\dashboard_server.py"
timeout /t 1 /nobreak >nul
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
  start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" "http://127.0.0.1:8765"
) else (
  start "" "http://127.0.0.1:8765"
)
endlocal
