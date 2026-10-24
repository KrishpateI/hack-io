<#
.SYNOPSIS
  Test-run recorder: runs backend/pipeline pytest, frontend lint/build/vitest and CDK
  tests/synth, then writes docs/test-results.md and docs/test-results.json (Req 44.13).

.PARAMETER Backend  Run only the backend section (combine flags to select several).
.PARAMETER Frontend Run only the frontend section.
.PARAMETER Infra    Run only the CDK section (offline; never needs AWS credentials).
Default (no flags): all sections. Missing tools are reported as SKIPPED, never as failures.
Exit code is 1 only if a step that actually ran failed.
#>
param(
  [switch]$Backend,
  [switch]$Frontend,
  [switch]$Infra,
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Docs = Join-Path $Root 'docs'
$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("safepath-tests-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
if (-not ($Backend -or $Frontend -or $Infra)) { $Backend = $Frontend = $Infra = $true }

$HypothesisProfile = if ($env:HYPOTHESIS_PROFILE) { $env:HYPOTHESIS_PROFILE } else { 'ci' }
$HypothesisExamples = if ($HypothesisProfile -eq 'ci') { 100 } elseif ($HypothesisProfile -eq 'demo-check') { 25 } else { 'unknown' }

$Steps = New-Object System.Collections.ArrayList

function Get-Tail([string]$file, [int]$n = 40) {
  if (Test-Path $file) { return ((Get-Content $file -Tail $n -ErrorAction SilentlyContinue) -join "`n") }
  return ''
}

function Add-Skipped($component, $name, $command, $reason) {
  [void]$Steps.Add([ordered]@{
    component = $component; name = $name; command = $command; status = 'SKIPPED'
    reason = $reason; exit_code = $null; duration_s = 0
    timestamp = (Get-Date).ToString('s'); junit = $null; output_tail = ''
  })
}

function Invoke-Step($component, $name, $display, $cmdline, $cwd, $junit = $null, $timeout = $TimeoutSeconds) {
  $log = Join-Path $Tmp ("$component-$name.log" -replace '[^\w\.-]', '_')
  Write-Host "==> [$component] $display" -ForegroundColor Cyan
  $start = Get-Date
  $p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/c', "`"$cmdline > `"$log`" 2>&1`"" `
    -WorkingDirectory $cwd -NoNewWindow -PassThru
  $null = $p.Handle  # cache the handle so ExitCode is readable on Windows PowerShell 5.1
  $status = 'PASSED'; $reason = ''
  if (-not $p.WaitForExit($timeout * 1000)) {
    & taskkill /PID $p.Id /T /F | Out-Null
    $status = 'FAILED'; $reason = "timed out after ${timeout}s"
  } elseif ($p.ExitCode -ne 0) {
    $status = 'FAILED'
  }
  $dur = [math]::Round(((Get-Date) - $start).TotalSeconds, 1)
  $code = if ($p.HasExited) { $p.ExitCode } else { $null }
  Write-Host "    $status in ${dur}s" -ForegroundColor $(if ($status -eq 'PASSED') { 'Green' } else { 'Red' })
  [void]$Steps.Add([ordered]@{
    component = $component; name = $name; command = $display; status = $status
    reason = $reason; exit_code = $code; duration_s = $dur
    timestamp = $start.ToString('s'); junit = $junit
    output_tail = $(if ($status -eq 'FAILED') { Get-Tail $log } else { '' })
  })
}

function Get-Version($exe, $argList) {
  $c = Get-Command $exe -ErrorAction SilentlyContinue
  if (-not $c) { return 'not installed' }
  try { return ((& $c.Source @argList 2>&1 | Select-Object -First 1) -as [string]).Trim() } catch { return 'unknown' }
}

$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$Versions = [ordered]@{ powershell = $PSVersionTable.PSVersion.ToString(); os = [System.Environment]::OSVersion.VersionString }
$Versions['python'] = if (Test-Path $Python) { Get-Version $Python @('--version') } else { 'backend venv missing' }
$Versions['node'] = Get-Version 'node' @('--version')
$Versions['npm'] = Get-Version 'npm' @('--version')
if (Test-Path $Python) {
  $pv = & $Python -c "import pytest, hypothesis; print(pytest.__version__, hypothesis.__version__)" 2>$null
  if ($pv) { $a = ($pv -split ' '); $Versions['pytest'] = $a[0]; $Versions['hypothesis'] = $a[1] }
}
$fePkg = Join-Path $Root 'frontend\package.json'
if (Test-Path $fePkg) {
  $j = Get-Content $fePkg -Raw | ConvertFrom-Json
  $Versions['vitest'] = $j.devDependencies.vitest
  $Versions['fast-check'] = $j.devDependencies.'fast-check'
}
$infraPkg = Join-Path $Root 'infrastructure\aws\package.json'
if (Test-Path $infraPkg) {
  $j = Get-Content $infraPkg -Raw | ConvertFrom-Json
  $Versions['aws-cdk'] = $j.devDependencies.'aws-cdk'
  $Versions['jest'] = $j.devDependencies.jest
}

# ---------------- Backend + pipeline ----------------
if ($Backend) {
  if (-not (Test-Path $Python)) {
    Add-Skipped 'Backend' 'pytest' 'python -m pytest -q' 'backend\.venv not found'
  } else {
    $xml = Join-Path $Tmp 'pytest.xml'
    Invoke-Step 'Backend+Pipeline' 'pytest' 'backend\.venv\Scripts\python.exe -m pytest -q --junitxml=<tmp>\pytest.xml' `
      "`"$Python`" -m pytest -q --junitxml=`"$xml`"" (Join-Path $Root 'backend') $xml
  }
}

# ---------------- Frontend ----------------
if ($Frontend) {
  $fe = Join-Path $Root 'frontend'
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    foreach ($n in 'lint', 'build', 'vitest') { Add-Skipped 'Frontend' $n "npm ($n)" 'npm not installed' }
  } elseif (-not (Test-Path (Join-Path $fe 'node_modules'))) {
    foreach ($n in 'lint', 'build', 'vitest') { Add-Skipped 'Frontend' $n "npm ($n)" 'frontend\node_modules missing (run npm ci)' }
  } else {
    Invoke-Step 'Frontend' 'lint' 'npm run lint' 'npm run lint' $fe
    Invoke-Step 'Frontend' 'build' 'npm run build' 'npm run build' $fe
    $vxml = Join-Path $Tmp 'vitest.xml'
    Invoke-Step 'Frontend' 'vitest' 'npm test -- --reporter=default --reporter=junit --outputFile.junit=<tmp>\vitest.xml' `
      "npm test -- --reporter=default --reporter=junit --outputFile.junit=`"$vxml`"" $fe $vxml
  }
}

# ---------------- CDK (offline) ----------------
if ($Infra) {
  $inf = Join-Path $Root 'infrastructure\aws'
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    foreach ($n in 'jest', 'cdk synth') { Add-Skipped 'Infra' $n "npm ($n)" 'npm not installed' }
  } elseif (-not (Test-Path (Join-Path $inf 'node_modules'))) {
    foreach ($n in 'jest', 'cdk synth') { Add-Skipped 'Infra' $n "npm ($n)" 'infrastructure\aws\node_modules missing (run npm ci)' }
  } else {
    $jxml = Join-Path $Tmp 'jest.xml'
    # jest has no built-in JUnit reporter; use its JSON output (parsed by test_report.py).
    $jxml = Join-Path $Tmp 'jest.json'
    Invoke-Step 'Infra' 'jest' 'npm test -- --json --outputFile=<tmp>\jest.json' `
      "npm test -- --json --outputFile=`"$jxml`"" $inf $jxml
    Invoke-Step 'Infra' 'cdk synth' 'npx cdk synth -q' 'npx cdk synth -q' $inf
    $out = Join-Path $inf 'cdk.out'
    if (Test-Path $out) { Remove-Item -Recurse -Force $out }
  }
}

# ---------------- Report ----------------
$manifest = [ordered]@{
  generated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
  tool_versions = $Versions
  settings = [ordered]@{
    'Hypothesis profile' = "$HypothesisProfile ($HypothesisExamples examples per property; tests/backend/conftest.py)"
    'fast-check numRuns' = 'set per property in frontend tests: 25 to 100 (see frontend/src/**/*.test.ts[x])'
  }
  steps = $Steps
}
$manifestPath = Join-Path $Tmp 'manifest.json'
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $manifestPath -Encoding UTF8

if (-not (Test-Path $Python)) { Write-Warning 'backend venv missing: cannot render report'; exit 1 }
New-Item -ItemType Directory -Force -Path $Docs | Out-Null
& $Python (Join-Path $PSScriptRoot 'test_report.py') --manifest $manifestPath `
  --out-md (Join-Path $Docs 'test-results.md') --out-json (Join-Path $Docs 'test-results.json')

$failed = @($Steps | Where-Object { $_.status -eq 'FAILED' }).Count
Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
if ($failed -gt 0) { exit 1 } else { exit 0 }
