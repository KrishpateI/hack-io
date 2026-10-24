# Shared helpers for deploy.ps1, teardown.ps1 and verify_deployment.ps1 (dot-sourced).
# Compatible with Windows PowerShell 5.1 and PowerShell 7. Nothing here runs AWS commands at load time.

$script:SafePathRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:InfraDir     = Join-Path $script:SafePathRoot 'infrastructure\aws'
$script:DiscoveryFile = Join-Path $script:InfraDir 'discovery.json'
$script:OutputsFile  = Join-Path $script:InfraDir 'cdk-outputs.json'
$script:HelperPy     = Join-Path $script:PSScriptRoot 'deploy_helpers.py'

function Get-PythonExe {
    $venv = Join-Path $script:SafePathRoot 'backend\.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw 'Python not found. Create backend\.venv first (see README).'
}

function Write-Step([string]$Text)  { Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Write-Info([string]$Text)  { Write-Host "   $Text" }
function Write-Warn([string]$Text)  { Write-Host "   WARNING: $Text" -ForegroundColor Yellow }
function Write-Plan([string]$Text)  { Write-Host "   [dry-run] $Text" -ForegroundColor DarkGray }
function Write-Fail([string]$Text)  { Write-Host "   ERROR: $Text" -ForegroundColor Red }

# Runs scripts/deploy_helpers.py and returns parsed JSON (or the raw string when -Raw).
function Invoke-Helper {
    param([string[]]$Arguments, [switch]$Raw)
    $py = Get-PythonExe
    $out = & $py $script:HelperPy @Arguments 2>&1
    $code = $LASTEXITCODE
    $text = ($out | ForEach-Object { "$_" }) -join "`n"
    if ($code -ne 0) { throw "deploy_helpers failed: $text" }
    if ($Raw) { return $text.Trim() }
    $parsed = $text | ConvertFrom-Json
    # Windows PowerShell 5.1 emits a JSON array as ONE object; unroll so callers can always use @(...).
    if ($parsed -is [array]) { foreach ($item in $parsed) { Write-Output $item }; return }
    return $parsed
}

function Format-Command([string[]]$Parts) {
    ($Parts | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
}

function Test-AwsCli { return [bool](Get-Command aws -ErrorAction SilentlyContinue) }

function Write-AwsCliInstructions {
    Write-Host ''
    Write-Host '   aws CLI not installed. Install and configure it, then re-run:' -ForegroundColor Yellow
    Write-Host '     1. Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html'
    Write-Host '     2. Sign in: `aws configure sso` (preferred) or `aws configure`, using a NON-root identity.'
    Write-Host '        Never paste access keys into this repository, a chat or a script.'
    Write-Host '     3. Re-run this script (still a dry run until you pass -Execute).'
}

# Calls `aws` (read-only or mutating, decided by the caller) and returns stdout as a string.
function Invoke-Aws {
    param([string[]]$Arguments, [string]$Region)
    $all = @($Arguments)
    if ($Region) { $all += @('--region', $Region) }
    $out = & aws @all 2>&1
    if ($LASTEXITCODE -ne 0) { throw "aws $($Arguments[0..1] -join ' ') failed: $(($out | ForEach-Object { "$_" }) -join ' ')" }
    return ($out | ForEach-Object { "$_" }) -join "`n"
}

# Read-only. Returns @{Account; Arn; IsRoot; Masked}.
function Get-AwsIdentity([string]$Region) {
    $json = Invoke-Aws -Arguments @('sts', 'get-caller-identity', '--output', 'json') -Region $Region | ConvertFrom-Json
    $isRoot = (Invoke-Helper -Arguments @('is-root', $json.Arn) -Raw) -eq 'true'
    $masked = Invoke-Helper -Arguments @('mask-account', $json.Account) -Raw
    return @{ Account = $json.Account; Arn = $json.Arn; IsRoot = $isRoot; Masked = $masked }
}

function Read-Discovery {
    if (-not (Test-Path $script:DiscoveryFile)) { throw "discovery.json missing: run backend\.venv\Scripts\python scripts\aws_discover.py" }
    return Get-Content $script:DiscoveryFile -Raw | ConvertFrom-Json
}

# Typed confirmation. Returns $true only when the typed text equals $Expected.
function Confirm-Typed {
    param([string]$Expected, [string]$Prompt, [string]$Preset, [switch]$Yes)
    if ($Yes) {
        if ($Preset -eq $Expected) { return $true }
        Write-Fail "-Yes needs -ConfirmPrefix $Expected (non-interactive confirmation must still name the prefix)."
        return $false
    }
    $typed = Read-Host $Prompt
    return ($typed -ceq $Expected)
}

# Run (or, in dry-run, print) a command line. Throws on a non-zero exit code.
function Invoke-Step {
    param([string[]]$Command, [string]$Cwd, [bool]$Execute)
    if (-not $Execute) { Write-Plan (Format-Command $Command); return }
    Write-Info ('$ ' + (Format-Command $Command))
    $exe = $Command[0]
    if ($exe -in @('npm', 'npx') -and (Get-Command "$exe.cmd" -ErrorAction SilentlyContinue)) { $exe = "$exe.cmd" }  # avoid .ps1 execution policy
    $rest = @(); if ($Command.Count -gt 1) { $rest = $Command[1..($Command.Count - 1)] }
    if ($Cwd) { Push-Location $Cwd }
    try {
        & $exe @rest
        if ($LASTEXITCODE -ne 0) { throw "command failed with exit code ${LASTEXITCODE}: $(Format-Command $Command)" }
    } finally { if ($Cwd) { Pop-Location } }
}
