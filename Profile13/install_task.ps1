[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$taskName = 'ChromePermanentHistory-Profile13'
$pythonw = 'C:\Users\sandu\AppData\Local\Programs\Python\Python312\pythonw.exe'
$collector = 'C:\ChromePermanentHistory\Profile13\history_collector.py'

if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "pythonw.exe was not found at $pythonw"
}
if (-not (Test-Path -LiteralPath $collector -PathType Leaf)) {
    throw "Collector was not found at $collector"
}

$taskCommand = ('"{0}" "{1}"' -f $pythonw, $collector)
& schtasks.exe /Create /TN $taskName /SC ONLOGON /TR $taskCommand /RL LIMITED /F

if ($LASTEXITCODE -eq 0) {
    & schtasks.exe /Run /TN $taskName
    Write-Output "Installed and started scheduled task: $taskName"
    exit 0
}

Write-Warning 'Task Scheduler did not allow this user to create a task. The installed Startup-folder launcher will start the collector at sign-in instead.'
exit 1
