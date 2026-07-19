# SelfAgent 桌面启动器（学 JackPro 本地启动体验）
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
  $py = ".\.venv\Scripts\python.exe"
} else {
  $py = "python"
}

& $py start.py @args
exit $LASTEXITCODE
