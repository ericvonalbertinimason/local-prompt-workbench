# Add the Prompt Workbench to the Claude desktop app's local MCP servers.
#
# Run it with Claude CLOSED (double-click add_to_claude_app.cmd). The app keeps its settings in
# memory and rewrites claude_desktop_config.json every few minutes while it runs, so an edit made
# while it is open is lost (seen 2026-10-21). This waits for Claude to exit, then
# claude_app_config.py backs the file up and adds only the prompt-workbench entry.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
$candidates = @(Get-ChildItem "$env:LOCALAPPDATA\Claude-Data\*\claude_desktop_config.json" -ErrorAction SilentlyContinue) +
              @(Get-ChildItem "$env:APPDATA\Claude\claude_desktop_config.json" -ErrorAction SilentlyContinue)
$config = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $config) { Write-Host 'Could not find claude_desktop_config.json.' -ForegroundColor Red; exit 1 }
if (-not (Test-Path $python)) { Write-Host "Python not found at $python" -ForegroundColor Red; exit 1 }

# The Claude app itself (...\app\Claude.exe, or ...\app-<version>\claude.exe for older installs),
# not Claude Code engines, which also run as claude.exe.
$running = { @(Get-Process -Name claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -match '\\app(-[\d.]+)?\\claude\.exe$' }) }
if ((& $running).Count) {
    Write-Host 'Claude is still open. Quit it from the system tray icon (right-click > Quit). Waiting up to 3 minutes...' -ForegroundColor Yellow
    for ($i = 0; $i -lt 90 -and (& $running).Count; $i++) { Start-Sleep -Seconds 2 }
    if ((& $running).Count) { Write-Host 'Claude is still running, so nothing was changed. Quit it and run this again.' -ForegroundColor Red; exit 1 }
}
Write-Host "Config: $($config.FullName)"
& $python (Join-Path $here 'claude_app_config.py') $config.FullName
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Start Claude again: prompt-workbench appears under Settings > Developer > Local MCP servers.' -ForegroundColor Green
