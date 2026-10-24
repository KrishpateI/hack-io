<#
.SYNOPSIS
  Post-deploy smoke tests against a deployed SafePath API. Read-only apart from one route calculation.

.DESCRIPTION
  GET /api/health, GET /api/graph/status and POST /api/routes/calculate. The URL comes from -ApiBaseUrl or
  from infrastructure\aws\cdk-outputs.json (HttpApiUrl). Only HTTP requests to that URL are made; no AWS API
  calls. Without a URL the script prints what it would check and exits 0.

  A 2xx or a JSON 4xx counts as "reachable" for the route request (a fresh deployment has no incident, so
  the expected answer is 404 unknown incident); a 5xx or a connection failure is a failure.
#>
[CmdletBinding()]
param(
    [string]$ApiBaseUrl,
    [string]$Prefix = 'safepath',
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest progress bar is very slow and noisy in 5.1
. (Join-Path $PSScriptRoot '_deploy_common.ps1')

if (-not $ApiBaseUrl -and (Test-Path $script:OutputsFile)) {
    $raw = Get-Content $script:OutputsFile -Raw | ConvertFrom-Json
    $api = $raw.PSObject.Properties | Where-Object { $_.Name -eq "$Prefix-api" } | Select-Object -First 1
    if ($api -and $api.Value.HttpApiUrl) { $ApiBaseUrl = [string]$api.Value.HttpApiUrl }
}
if (-not $ApiBaseUrl) {
    Write-Info 'No -ApiBaseUrl and no cdk-outputs.json: nothing to check. Planned checks:'
    Write-Plan 'GET  <api>/api/health            expect 200 and status=ok'
    Write-Plan 'GET  <api>/api/graph/status      expect 200 (status ok, or fallback/INSUFFICIENT_DATA reported honestly)'
    Write-Plan 'POST <api>/api/routes/calculate  expect 2xx, or 4xx JSON when no incident exists yet'
    exit 0
}
if ($ApiBaseUrl -notmatch '^(https://[A-Za-z0-9.\-]+|http://(localhost|127\.0\.0\.1))(:\d+)?(/[A-Za-z0-9._~\-/]*)?$') {
    Write-Fail 'ApiBaseUrl must be an https URL (plain http is accepted for localhost only) without a query string.'; exit 2
}
$base = $ApiBaseUrl.TrimEnd('/')
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$failures = 0
function Invoke-Check([string]$Name, [string]$Method, [string]$Path, $Body, [int[]]$Accept) {
    $url = "$base$Path"
    $status = 0; $text = ''
    try {
        $p = @{ Uri = $url; Method = $Method; UseBasicParsing = $true; TimeoutSec = $TimeoutSec }
        if ($Body) { $p.Body = ($Body | ConvertTo-Json -Depth 6 -Compress); $p.ContentType = 'application/json' }
        $r = Invoke-WebRequest @p
        $status = [int]$r.StatusCode; $text = $r.Content
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            $status = [int]$resp.StatusCode
            try { $sr = New-Object IO.StreamReader($resp.GetResponseStream()); $text = $sr.ReadToEnd() } catch { }
        } else { $text = $_.Exception.Message }
    }
    $ok = ($Accept -contains $status)
    $tag = if ($ok) { 'PASS' } else { 'FAIL' }
    $color = if ($ok) { 'Green' } else { 'Red' }
    $snippet = if ($text.Length -gt 160) { $text.Substring(0, 160) + '...' } else { $text }
    Write-Host ("   [{0}] {1} {2} -> {3}  {4}" -f $tag, $Method, $Path, $status, $snippet) -ForegroundColor $color
    if (-not $ok) { $script:failures++ }
    return $text
}

Write-Step "Smoke tests: $base"
$health = Invoke-Check 'health' 'GET' '/api/health' $null @(200)
try { $h = $health | ConvertFrom-Json; if ($h.status -ne 'ok') { Write-Warn "health status is '$($h.status)'"; } } catch { }
$null = Invoke-Check 'graph' 'GET' '/api/graph/status' $null @(200)

# Use an existing incident when there is one, otherwise a placeholder id (expect a clean 404).
$incidentId = 'inc_smoketest'
try {
    $list = Invoke-WebRequest -Uri "$base/api/incidents" -UseBasicParsing -TimeoutSec $TimeoutSec | Select-Object -ExpandProperty Content | ConvertFrom-Json
    if (@($list).Count -gt 0) { $incidentId = @($list)[0].incident_id }
} catch { }
$body = @{
    incident_id = $incidentId
    origin = @{ latitude = 41.8827; longitude = -87.6233 }
    destination = @{ latitude = 41.8835; longitude = -87.6350 }
    travel_mode = 'walking'; mobility_profile = 'standard'; alternatives = 1
}
$null = Invoke-Check 'route' 'POST' '/api/routes/calculate' $body @(200, 400, 404, 409, 422)

Write-Host ''
if ($failures -eq 0) { Write-Host 'All smoke tests passed.' -ForegroundColor Green; exit 0 }
Write-Host "$failures smoke test(s) failed. Check CloudWatch logs for <prefix>-api." -ForegroundColor Red
exit 1
