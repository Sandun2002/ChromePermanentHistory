[CmdletBinding()]
param(
    [switch]$NoPause
)

$collectorPath = 'C:\ChromePermanentHistory\Profile13\history_collector.py'
$logPath = 'C:\ChromePermanentHistory\Profile13\collector.log'
$archivePath = 'C:\ChromePermanentHistory\Profile13\PermanentChromeHistory.sqlite'

$processes = @(
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*$collectorPath*" }
)

Write-Host ''
Write-Host 'Permanent Chrome History Collector - Status' -ForegroundColor Cyan
Write-Host ''

if ($processes.Count -eq 1) {
    Write-Host 'Status: RUNNING' -ForegroundColor Green
    Write-Host ('Process ID: {0}' -f $processes[0].ProcessId)
}
elseif ($processes.Count -eq 0) {
    Write-Host 'Status: NOT RUNNING' -ForegroundColor Red
}
else {
    Write-Host ('Status: WARNING - {0} collector processes are running' -f $processes.Count) -ForegroundColor Yellow
}

if (Test-Path -LiteralPath $logPath -PathType Leaf) {
    $logFile = Get-Item -LiteralPath $logPath
    $age = [Math]::Round(((Get-Date) - $logFile.LastWriteTime).TotalSeconds)
    $latestLog = Get-Content -LiteralPath $logPath -Tail 1
    Write-Host ('Last collector update: {0}' -f $logFile.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    Write-Host ('Seconds since update: {0}' -f $age)
    Write-Host ('Latest log: ' + $latestLog)
}
else {
    Write-Host 'Collector log: not found' -ForegroundColor Yellow
}

if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
    Write-Host 'Archive database: available' -ForegroundColor Green
}
else {
    Write-Host 'Archive database: missing' -ForegroundColor Red
}

Write-Host ''
if (-not $NoPause) {
    Read-Host 'Press Enter to close'
}
