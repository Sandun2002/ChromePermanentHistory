@echo off
setlocal

rem Start the collector through its scheduled task. If the task has not been
rem installed yet, start the same collector directly in the background.
schtasks.exe /Run /TN "ChromePermanentHistory-Profile13" >nul 2>&1
if errorlevel 1 (
    start "" "C:\Users\sandu\AppData\Local\Programs\Python\Python312\pythonw.exe" "C:\ChromePermanentHistory\Profile13\history_collector.py"
)

rem Open exactly the profile folder named Profile 13.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 13"

endlocal
