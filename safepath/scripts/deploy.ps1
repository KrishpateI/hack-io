<#
.SYNOPSIS
  Deploy SafePath to a sandbox AWS account. SAFE BY DEFAULT: prints the plan and changes nothing
  unless -Execute is passed.

.DESCRIPTION
  Stages (run all, or one with -Stage):
    preflight  aws CLI, caller identity (read-only), not root, discovery gate, cost/risk summary, typed confirmation
    build      package the Lambda zip (scripts/package_lambda.py), install CDK deps if missing
    bootstrap  `cdk bootstrap` ONLY with -Bootstrap (creates the CDKToolkit stack, an S3 bucket and IAM roles)
    deploy     `cdk deploy --all --require-approval broadening` with context from discovery.json
    frontend   (two-phase) npm ci + build with VITE_* values taken from the deployed stack outputs
    upload     graph artifact (latest.json LAST), PMTiles and frontend bundle with Content-Type/Cache-Control
    verify     smoke tests via scripts/verify_deployment.ps1

  Nothing here is run by the build or the test-suite. No AWS call is made without -Execute, and the
  only AWS calls made before the typed confirmation are read-only (sts get-caller-identity,
  cloudformation describe-stacks).

.EXAMPLE
  .\scripts\deploy.ps1 -Region us-east-1                      # dry run: prints the plan
  .\scripts\deploy.ps1 -Region us-east-1 -Execute -Bootstrap -AlertEmail me@example.com
#>
[CmdletBinding()]
param(
    [string]$Region,
    [string]$Prefix = 'safepath',
    [string]$AlertEmail,
    [string]$Profile,
    [ValidateSet('all', 'preflight', 'build', 'bootstrap', 'deploy', 'frontend', 'upload', 'verify')]
    [string]$Stage = 'all',
    [switch]$Execute,
    [Alias('WhatIf')][switch]$DryRun,
    [switch]$Bootstrap,
    [switch]$Yes,
    [string]$ConfirmPrefix,
    [string]$RealtimeMode = 'sse',
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_deploy_common.ps1')

if ($Execute -and $DryRun) { Write-Fail '-Execute and -DryRun/-WhatIf are mutually exclusive.'; exit 2 }
$doIt = [bool]$Execute          # default (no -Execute) is a dry run
if (-not $Region) { Write-Fail '-Region is required (for example -Region us-east-1). It is never guessed.'; exit 2 }
try { Invoke-Helper -Arguments @('validate', '--prefix', $Prefix, '--region', $Region) | Out-Null
      if ($AlertEmail) { Invoke-Helper -Arguments @('validate', '--email', $AlertEmail) | Out-Null } }
catch { Write-Fail $_.Exception.Message; exit 2 }

if ($Profile) { $env:AWS_PROFILE = $Profile }
$env:AWS_REGION = $Region; $env:AWS_DEFAULT_REGION = $Region

function Want([string]$name) { return ($Stage -eq 'all' -or $Stage -eq $name) }

Write-Host ''
if ($doIt) { Write-Host 'MODE: EXECUTE (changes will be made after confirmation)' -ForegroundColor Red }
else { Write-Host 'MODE: DRY RUN (nothing is created, uploaded or deleted). Pass -Execute to deploy.' -ForegroundColor Green }
Write-Info "prefix=$Prefix region=$Region stage=$Stage bootstrap=$([bool]$Bootstrap)"

$account = '<account-id>'
$ctxFile = Join-Path ([IO.Path]::GetTempPath()) "safepath-ctx-$([guid]::NewGuid().ToString('N')).json"
$stopHere = $false

try {
    # ------------------------------------------------------------------ 1. preflight
    Write-Step '1/7 Preflight'
    $haveAws = Test-AwsCli
    if (-not $haveAws) {
        Write-Info 'aws CLI: NOT FOUND'
        Write-AwsCliInstructions
        if ($doIt) { Write-Fail 'Cannot execute without the aws CLI.'; exit 1 }
        Write-Info 'Dry run continues below so you can review the plan.'
    } else {
        Write-Info 'aws CLI: found'
        try {
            $id = Get-AwsIdentity -Region $Region
            $account = $id.Account
            Write-Info "account: $($id.Masked)   region: $Region"
            Write-Info "identity: $($id.Arn -replace '\d{12}', '************')"
            if ($id.IsRoot) {
                Write-Fail 'The caller is the account ROOT user. Use an IAM Identity Center / IAM user or role instead.'
                if ($doIt) { exit 1 }
            } else { Write-Info 'identity is NOT root: ok' }
        } catch {
            Write-Fail "could not read caller identity (are credentials configured? try `aws configure sso`): $($_.Exception.Message)"
            if ($doIt) { exit 1 }
        }
    }

    # discovery gate
    if ($doIt -and $haveAws) {
        Write-Info 're-running read-only discovery (scripts/aws_discover.py)...'
        & (Get-PythonExe) (Join-Path $PSScriptRoot 'aws_discover.py') --region $Region
    }
    $disc = Read-Discovery
    Write-Info "discovery.json: status=$($disc.status) deployable=$($disc.deployable) generated=$($disc.generated_at)"
    if ($disc.reason) { Write-Info "reason: $($disc.reason)" }
    foreach ($s in @($disc.substitutions)) { Write-Info ("  {0}: {1} -> {2}" -f $s.feature, $s.state, $s.substitute) }
    if (-not $disc.deployable) {
        Write-Fail 'Discovery gate says deployable=false. Fix credentials/permissions and re-run scripts\aws_discover.py.'
        if ($doIt) { exit 1 }
        Write-Info '(dry run: continuing; -Execute would stop here)'
    }

    # context flags
    $ctxArgs = @('context-args', '--discovery', $script:DiscoveryFile, '--prefix', $Prefix, '--region', $Region)
    if ($AlertEmail) { $ctxArgs += @('--alert-email', $AlertEmail) }
    $ctx = @(Invoke-Helper -Arguments $ctxArgs)
    Write-Info ("CDK context: " + (($ctx | Where-Object { $_ -ne '-c' }) -join ' '))
    ConvertTo-Json -InputObject $ctx -Compress | Set-Content -Path $ctxFile -Encoding ASCII

    # cost / risk summary
    Write-Host ''
    Write-Host '   COST / RISK SUMMARY' -ForegroundColor Yellow
    Write-Info "Will create (tagged Project=SafePath, Simulated=true), all stacks named ${Prefix}-*:"
    Write-Info "  ${Prefix}-data          17 DynamoDB tables (on-demand), graphs + photos S3 buckets (private, encrypted)"
    Write-Info "  ${Prefix}-api           API Lambda (3008 MB), sweeper Lambda (every minute), HTTP API, WebSocket API, 2 secrets"
    Write-Info "  ${Prefix}-web           private site bucket (+ CloudFront only if enableCloudFront=true)"
    Write-Info "  ${Prefix}-observability CloudWatch dashboard and alarms (+ SNS email topic when -AlertEmail is given)"
    if (($disc.context_flags.enableBudget -eq 'true') -and $AlertEmail) { Write-Info "  ${Prefix}-budget        monthly 25 USD budget with 80%/100% email alerts" }
    else { Write-Warn 'No AWS Budget will be created (needs enableBudget=true and -AlertEmail). Watch cost in the console.' }
    Write-Info 'Cost: designed to be small and mostly free-tier friendly (on-demand DynamoDB, Lambda, 7-day logs).'
    Write-Info '  Items that are NOT free: Secrets Manager (2 secrets, about 0.80 USD/month), the 1-minute sweeper'
    Write-Info '  Lambda invocations, CloudWatch alarms/dashboard beyond the free tier, CloudFront data if enabled.'
    Write-Info '  Estimate: a few USD/month idle in most accounts. This is an estimate, not a quote. Tear down when done.'
    Write-Warn 'The graphs, photos and site buckets are tagged for DESTROY: teardown deletes their contents and the buckets.'
    Write-Warn 'Demo mode is OFF in AWS by default. Do not use real personal data: the deployment is a sandbox demo.'

    if ($doIt) {
        if (-not (Confirm-Typed -Expected $Prefix -Prompt "Type the stack prefix '$Prefix' to confirm deployment to account $($id.Masked) in $Region" -Preset $ConfirmPrefix -Yes:$Yes)) {
            Write-Fail 'Confirmation did not match. Nothing was changed.'; exit 1
        }
        $env:CDK_DEFAULT_ACCOUNT = $account; $env:CDK_DEFAULT_REGION = $Region
    }
    if ($Stage -eq 'preflight') { $stopHere = $true }

    # ------------------------------------------------------------------ 2. build (backend package)
    if (-not $stopHere -and (Want 'build')) {
        Write-Step '2/7 Build: Lambda package'
        $py = Get-PythonExe
        Invoke-Step -Command @($py, (Join-Path $PSScriptRoot 'package_lambda.py')) -Cwd $script:SafePathRoot -Execute $doIt
        if (-not (Test-Path (Join-Path $script:InfraDir 'node_modules'))) {
            Invoke-Step -Command @('npm', 'ci') -Cwd $script:InfraDir -Execute $doIt
        }
    }

    # ------------------------------------------------------------------ 3. bootstrap (explicit opt-in)
    if (-not $stopHere -and (Want 'bootstrap')) {
        Write-Step '3/7 CDK bootstrap'
        if (-not $Bootstrap) {
            Write-Info 'Skipped (pass -Bootstrap to run it).'
            if ($doIt -and $haveAws) {
                $has = $true
                try { Invoke-Aws -Arguments @('cloudformation', 'describe-stacks', '--stack-name', 'CDKToolkit', '--output', 'json') -Region $Region | Out-Null }
                catch { $has = $false }
                if (-not $has) {
                    Write-Fail "No CDKToolkit stack in $Region and -Bootstrap was not given. CDK cannot upload the Lambda package without it."
                    Write-Info 'See docs/aws-deployment.md ("Bootstrap") for what it creates and the alternatives.'
                    exit 1
                }
                Write-Info 'CDKToolkit stack found: ok'
            }
        } else {
            Write-Warn "cdk bootstrap creates the CDKToolkit stack in ${Region}: a staging S3 bucket, an ECR repository and IAM roles."
            Write-Warn 'These are NOT tagged Project=SafePath, so teardown.ps1 will not remove them (they may be shared).'
            $bc = @(Invoke-Helper -Arguments @('bootstrap-cmd', '--account', $(if ($account -match '^\d{12}$') { $account } else { '000000000000' }), '--region', $Region))
            if ($account -notmatch '^\d{12}$') { $bc[-1] = "aws://$account/$Region" }
            Invoke-Step -Command $bc -Cwd $script:InfraDir -Execute $doIt
        }
    }

    # ------------------------------------------------------------------ 4. deploy
    if (-not $stopHere -and (Want 'deploy')) {
        Write-Step '4/7 CDK deploy'
        $dc = @(Invoke-Helper -Arguments @('deploy-cmd', '--prefix', $Prefix, '--ctx-file', $ctxFile))
        Write-Info 'Failed stacks roll back automatically (CloudFormation removes only what that deployment created).'
        Invoke-Step -Command $dc -Cwd $script:InfraDir -Execute $doIt
    }

    # outputs (needed by the two-phase frontend build and the upload)
    $flat = @{}
    if ($doIt -and (Test-Path $script:OutputsFile)) {
        $raw = Get-Content $script:OutputsFile -Raw | ConvertFrom-Json
        foreach ($p in $raw.PSObject.Properties) {
            if ($p.Name -eq $Prefix -or $p.Name.StartsWith("$Prefix-")) {
                foreach ($o in $p.Value.PSObject.Properties) { if (-not $flat.ContainsKey($o.Name)) { $flat[$o.Name] = [string]$o.Value } }
            }
        }
    }

    # ------------------------------------------------------------------ 5. frontend (phase two)
    $distDir = Join-Path $script:SafePathRoot 'frontend\dist'
    if (-not $stopHere -and -not $SkipFrontend -and (Want 'frontend')) {
        Write-Step '5/7 Build frontend with real public URLs (two-phase)'
        $feDir = Join-Path $script:SafePathRoot 'frontend'
        if ($doIt) {
            if ($flat.Count -eq 0) { Write-Fail "No stack outputs in $script:OutputsFile; run the deploy stage first."; exit 1 }
            $tmpOut = Join-Path ([IO.Path]::GetTempPath()) "safepath-out-$([guid]::NewGuid().ToString('N')).json"
            try {
                Copy-Item $script:OutputsFile $tmpOut
                $envMap = Invoke-Helper -Arguments @('frontend-env', '--outputs-file', $tmpOut, '--prefix', $Prefix, '--realtime', $RealtimeMode)
            } finally { Remove-Item $tmpOut -ErrorAction SilentlyContinue }
            foreach ($p in $envMap.PSObject.Properties) { Set-Item -Path "env:$($p.Name)" -Value $p.Value; Write-Info "$($p.Name)=$($p.Value)" }
        } else {
            Write-Plan 'VITE_API_BASE_URL=<ApiViaCloudFront or HttpApiUrl from the stack outputs>   (public identifier only)'
            Write-Plan "VITE_REALTIME_MODE=$RealtimeMode   VITE_PMTILES_URL=<SiteUrl>/tiles/chicago.pmtiles (CloudFront only)"
        }
        Invoke-Step -Command @('npm', 'ci') -Cwd $feDir -Execute $doIt
        Invoke-Step -Command @('npm', 'run', 'build') -Cwd $feDir -Execute $doIt
    }

    # ------------------------------------------------------------------ 6. upload
    if (-not $stopHere -and (Want 'upload')) {
        Write-Step '6/7 Upload artifacts'
        $graphsBucket = '<graphs-bucket>'; $siteBucket = '<site-bucket>'; $hosting = $true
        if ($doIt) {
            $graphsBucket = $flat['GraphsBucketName']
            if (-not $graphsBucket) { Write-Fail 'GraphsBucketName missing from stack outputs.'; exit 1 }
            if ($flat.ContainsKey('PublicWebsiteBucketName')) { $siteBucket = $flat['PublicWebsiteBucketName'] }
            elseif ($flat.ContainsKey('DistributionDomainName')) { $siteBucket = $flat['SiteBucketName'] }
            else { $siteBucket = $null; $hosting = $false }
            $null = Invoke-Helper -Arguments @('check-stack', '--prefix', $Prefix, "$Prefix-data")
        }
        $planArgs = @('upload-plan', '--graphs', (Join-Path $script:SafePathRoot 'data\processed\graphs'), '--graphs-bucket', $graphsBucket)
        $tiles = Join-Path $script:SafePathRoot 'data\processed\tiles\chicago.pmtiles'
        if ($hosting) {
            $planArgs += @('--site-bucket', $siteBucket)
            if (Test-Path $tiles) { $planArgs += @('--pmtiles', $tiles) } else { Write-Warn "PMTiles not found at $tiles; basemap will not be uploaded." }
            if (Test-Path $distDir) { $planArgs += @('--dist', $distDir) } else { Write-Info 'frontend\dist not built yet (it is built after the deploy); frontend files are not in this plan.' }
        } else {
            Write-Info 'No web hosting enabled (no CloudFront, no s3-website opt-in): only the graph artifact is uploaded.'
        }
        $plan = @(Invoke-Helper -Arguments $planArgs)
        Write-Info "$($plan.Count) objects. Order: graph version files, manifest.json, then latest.json LAST; then tiles; frontend with index.html last."
        if ($doIt) {
            foreach ($u in $plan) { Invoke-Step -Command @($u.command) -Execute $true | Out-Null }
        } else {
            $show = @($plan | Select-Object -First 3) + @($plan | Select-Object -Last 2)
            foreach ($u in $show) { Write-Plan (Format-Command @($u.command)) }
            $graphObjs = @($plan | Where-Object { $_.bucket -eq $graphsBucket })
            Write-Plan "(graph group: $($graphObjs.Count) objects, last one is $($graphObjs[-1].key))"
        }
    }

    # ------------------------------------------------------------------ 7. verify + guidance
    if (-not $stopHere -and (Want 'verify')) {
        Write-Step '7/7 Smoke tests and URLs'
        if ($doIt -and $flat.ContainsKey('HttpApiUrl')) {
            $base = if ($flat.ContainsKey('ApiViaCloudFront')) { $flat['ApiViaCloudFront'] -replace '/api$', '' } else { $flat['HttpApiUrl'] }
            & (Join-Path $PSScriptRoot 'verify_deployment.ps1') -ApiBaseUrl $base
            Write-Info "HTTP API:  $($flat['HttpApiUrl'])"
            if ($flat.ContainsKey('WebSocketUrl')) { Write-Info "WebSocket: $($flat['WebSocketUrl'])" }
            if ($flat.ContainsKey('SiteUrl')) { Write-Info "Site:      $($flat['SiteUrl'])" }
            if ($flat.ContainsKey('PublicWebsiteUrl')) { Write-Info "Site (HTTP only, public): $($flat['PublicWebsiteUrl'])" }
            if (-not ($flat.ContainsKey('SiteUrl') -or $flat.ContainsKey('PublicWebsiteUrl'))) { Write-Info 'No hosted site: run the frontend locally (cd frontend; npm run dev) with VITE_API_BASE_URL set to the HTTP API URL.' }
        } else {
            Write-Plan 'scripts\verify_deployment.ps1 -ApiBaseUrl <HttpApiUrl>  (GET /api/health, GET /api/graph/status, POST /api/routes/calculate)'
        }
        Write-Host ''
        Write-Host '   DEMO DATA (optional, risky)' -ForegroundColor Yellow
        Write-Info 'SAFEPATH_DEMO_MODE is OFF on the Lambda by default, so /api/demo/* returns 404 and no demo sessions exist.'
        Write-Info 'To try the demo scenarios temporarily: set SAFEPATH_DEMO_MODE=true on the API function'
        Write-Info "  (console: Lambda -> ${Prefix}-api -> Configuration -> Environment variables), then POST /api/demo/sessions."
        Write-Info 'Risk: anyone with the URL can mint moderator-level demo sessions while it is on. Turn it off again'
        Write-Info '  (delete the variable) right after your demo, or redeploy, which resets it. Never enable it with real data.'
    }
    Write-Host ''
    if ($doIt) { Write-Host 'Done.' -ForegroundColor Green } else { Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Green }
    exit 0
}
catch {
    Write-Fail $_.Exception.Message
    exit 1
}
finally {
    Remove-Item $ctxFile -ErrorAction SilentlyContinue
}
