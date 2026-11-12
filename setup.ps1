# Prompt Workbench: bring everything up and verify it. Safe to run any time.
#
#   Double-click start.cmd, or:  powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Idempotent: it only starts or creates what is missing, and never deletes anything or
# replaces a running container. It does not change Windows or Docker settings; it tells
# you when one of those would help.
param(
    [switch]$SkipWarmup,          # skip loading the model into GPU memory at the end
    [switch]$RegisterClaudeCode   # also add the workbench MCP server to Claude Code (user scope)
)
# 'Continue', not 'Stop': in Windows PowerShell 5.1, redirecting a native command's stderr
# under 'Stop' turns docker compose's normal progress output into a terminating error.
# Failures are checked explicitly with $LASTEXITCODE and the Fail function instead.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
# The history database must live outside OneDrive: a synced SQLite file can be overwritten
# or corrupted, and a copy of the project there starts the stack on its own stale data.
# On 2026-10-04 start.cmd in the Desktop copy did exactly that. The Desktop copy's
# start.cmd now launches the real project instead.
if ($Root -match '\\OneDrive') {
    Write-Host "This copy is in OneDrive ($Root). Start the workbench from the real project instead:" -ForegroundColor Red
    Write-Host '   C:\path\to\prompt-workbench\start.cmd'
    exit 1
}
Set-Location $Root
$Ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$DockerDesktop = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
if (-not (Test-Path $DockerDesktop)) { $DockerDesktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" }
$Model = 'qwen3.5-assistant-fast:4b'
$BaseModel = 'qwen3.5:4b'
$notes = New-Object System.Collections.Generic.List[string]

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   ok    $text" -ForegroundColor Green }
function Did($text) { Write-Host "   done  $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "   FAIL  $text" -ForegroundColor Red; exit 1 }
function DockerUp { docker info *> $null; return ($LASTEXITCODE -eq 0) }
function Listening($port) { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function WaitHttp($url, $seconds, $hostHeader) {
    for ($i = 0; $i -lt $seconds; $i += 2) {
        try {
            $h = @{}; if ($hostHeader) { $h['Host'] = $hostHeader }
            if ((Invoke-WebRequest -Uri $url -Headers $h -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 2
    }
    return $false
}

Step 'Prerequisites'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker Desktop is not installed: https://www.docker.com/products/docker-desktop/' }
Ok 'Docker CLI found'
if (-not (Test-Path $Ollama)) { Fail 'Ollama is not installed: https://ollama.com/download' }
Ok 'Ollama found'

Step 'Docker engine'
if (DockerUp) { Ok 'running' } else {
    Start-Process $DockerDesktop
    Did 'started Docker Desktop, waiting for the engine...'
    $up = $false
    for ($i = 0; $i -lt 45 -and -not $up; $i++) { Start-Sleep -Seconds 4; $up = DockerUp }
    if (-not $up) {
        Write-Host '   Engine did not come up; trying the stale-socket recovery (see README.md).'
        powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'bench\recover_docker.ps1')
        if (-not (DockerUp)) { Fail 'Docker engine still not running. In any Docker error dialog choose Quit, never Reset to factory defaults.' }
    }
    Ok 'engine up'
}
try {
    $settings = Get-Content "$env:APPDATA\Docker\settings-store.json" -Raw | ConvertFrom-Json
    if (-not $settings.AutoStart) { $notes.Add('Docker Desktop -> Settings -> General: turn on "Start Docker Desktop when you sign in" (otherwise the workbench is down after every reboot).') }
    if ($settings.UseResourceSaver -ne $false) { $notes.Add('Docker Desktop -> Settings -> Resources: turn off Resource Saver (it stops the stack when idle).') }
} catch {}

Step 'Ollama'
if (Listening 11434) { Ok 'serving on 11434' } else {
    # Process-level settings only; the permanent ones are user environment variables.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Ollama; $psi.Arguments = 'serve'; $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables['OLLAMA_KEEP_ALIVE'] = '-1'
    $psi.EnvironmentVariables['OLLAMA_MAX_LOADED_MODELS'] = '2'
    [System.Diagnostics.Process]::Start($psi) | Out-Null
    if (-not (WaitHttp 'http://127.0.0.1:11434/api/tags' 30)) { Fail 'Ollama did not start' }
    Did 'started ollama serve'
}
if ([Environment]::GetEnvironmentVariable('OLLAMA_KEEP_ALIVE', 'User') -ne '-1') {
    $notes.Add('Set the user environment variable OLLAMA_KEEP_ALIVE=-1 so the model stays loaded (first request otherwise waits ~7s).')
}

Step 'Models'
$models = & $Ollama list
if (-not ($models -match [regex]::Escape($BaseModel))) {
    Write-Host "   $BaseModel is missing; downloading it (about 3.4 GB)..."
    & $Ollama pull $BaseModel
    if ($LASTEXITCODE -ne 0) { Fail "could not pull $BaseModel" }
    Did "pulled $BaseModel"
} else { Ok "$BaseModel present" }
$current = ''
if ($models -match [regex]::Escape($Model)) { $current = (& $Ollama show --modelfile $Model) -join "`n" }
if ($current -notmatch 'num_ctx 65536') {
    # Both pipeline stages must use the same context size, or Ollama reloads between them.
    & $Ollama create $Model -f (Join-Path $Root 'outputs\Qwen-Assistant-Fast.Modelfile')
    if ($LASTEXITCODE -ne 0) { Fail "could not create $Model" }
    Did "created $Model (64K context)"
} else { Ok "$Model present with 64K context" }

Step 'Containers'
$names = docker ps -a --format '{{.Names}}'
# n8n was retired on 2026-10-28: the workbench runs its own pipeline and keeps the saved library.
# Its data volume and backups/n8n_data-20261028-retired.tgz remain; set $env:PIPELINE = 'n8n'
# before running this to bring it back (see README.md "n8n retired").
if ($env:PIPELINE -ne 'n8n') { Ok 'n8n retired (not needed)' }
elseif ($names -contains 'n8n') {
    if ((docker inspect -f '{{.State.Running}}' n8n) -ne 'true') { docker start n8n | Out-Null; Did 'started n8n' } else { Ok 'n8n running' }
} else {
    docker volume inspect n8n_data *> $null
    if ($LASTEXITCODE -ne 0) { docker volume create n8n_data | Out-Null; Did 'created volume n8n_data' }
    docker compose -f outputs\n8n.compose.yml --profile rollback up -d
    if (-not (WaitHttp 'http://127.0.0.1:5678/' 90)) { Fail 'n8n did not start' }
    docker cp outputs\prompt-assistant.n8n.json n8n:/tmp/wf_import.json
    docker exec n8n n8n import:workflow --input=/tmp/wf_import.json | Out-Null
    docker exec n8n n8n publish:workflow --id=PromptAssistant20260909 | Out-Null
    docker restart n8n | Out-Null
    Did 'created n8n, imported and published the workflow'
    $notes.Add('Fresh n8n: open http://127.0.0.1:5678 once to create the owner account, add an "Ollama" credential with base URL http://host.docker.internal:11434, and create the saved-prompt data table if you want the library.')
}
docker compose -f outputs\prompt-optimizer.compose.yml up -d 2>$null | Out-Null
Ok 'prompt-optimizer up'
docker image inspect harness-workbench *> $null
if ($LASTEXITCODE -ne 0) { docker compose -f harness\compose.yml up -d --build | Out-Null; Did 'built and started prompt-workbench' }
else { docker compose -f harness\compose.yml up -d 2>$null | Out-Null; Ok 'prompt-workbench up' }
if (-not (WaitHttp 'http://127.0.0.1:8789/' 60 '127.0.0.1:8789')) { Fail 'workbench did not answer on 8789' }

if (-not $SkipWarmup) {
    Step 'Warm-up'
    $body = @{ model = $Model; prompt = 'Say OK.'; stream = $false; think = $false; options = @{ num_ctx = 65536; num_predict = 4 } } | ConvertTo-Json
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 180 | Out-Null; Ok 'model loaded into GPU memory' }
    catch { Write-Host "   warm-up skipped: $($_.Exception.Message)" }
}

Step 'Versions'
# Read-only: compares installed versions with the latest releases (cached for a day, skipped
# offline). Upgrades are done one at a time behind the quality gate (README.md).
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) { python bench/versions.py } else { Write-Host '   (Python not found; skipped)' }

Step 'Health'
$bash = Get-Command bash -ErrorAction SilentlyContinue
if ($bash) { bash bench/health.sh } else { Write-Host '   (install Git for Windows to run bench/health.sh)' }

Step 'Claude Code'
$McpUrl = 'http://127.0.0.1:8789/mcp'
$claude = Get-Command claude -ErrorAction SilentlyContinue
# The native installer puts claude.exe in ~\.local\bin, which is not always on PATH.
if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { Set-Alias claude "$env:USERPROFILE\.local\bin\claude.exe"; $claude = $true }
if (-not $claude) {
    Write-Host '   Claude Code CLI not found; skip (install it, then run setup.ps1 -RegisterClaudeCode).'
} else {
    claude mcp get prompt-workbench *> $null
    if ($LASTEXITCODE -eq 0) { Ok 'prompt-workbench MCP server already added' }
    elseif ($RegisterClaudeCode) {
        claude mcp add --transport http --scope user prompt-workbench $McpUrl
        if ($LASTEXITCODE -eq 0) { Did 'added prompt-workbench to Claude Code (user scope)' } else { Write-Host '   could not add it; run the command below yourself.' }
    } else {
        $notes.Add("Use it from Claude Code: run setup.ps1 -RegisterClaudeCode, or: claude mcp add --transport http --scope user prompt-workbench $McpUrl")
    }
}

Write-Host "`nReady: http://127.0.0.1:8789" -ForegroundColor Green
if ($notes.Count) {
    Write-Host "`nRecommended:" -ForegroundColor Yellow
    foreach ($n in $notes) { Write-Host "  - $n" }
}
