<#
.SYNOPSIS
  Remove the SafePath deployment. DRY RUN by default; -Execute is required to delete anything.

.DESCRIPTION
  Hard safety rules (never delete unrelated AWS resources):
    * only CloudFormation stacks whose name is one of <prefix>-data|api|web|observability|budget AND that
      carry the tag Project=SafePath are considered;
    * only S3 buckets that belong to those stacks, are named <prefix>-(graphs|photos|site|public-website)-...
      AND carry the tag Project=SafePath are emptied (all object versions and delete markers);
    * the CDKToolkit bootstrap stack and its bucket are never touched;
    * the plan is printed first and you must type the prefix to confirm.
  Read-only AWS calls (sts, cloudformation describe/list, s3api get-bucket-tagging) are used to build the
  plan, also in dry-run mode.
#>
[CmdletBinding()]
param(
    [string]$Region,
    [string]$Prefix = 'safepath',
    [string]$Profile,
    [switch]$Execute,
    [Alias('WhatIf')][switch]$DryRun,
    [switch]$Yes,
    [string]$ConfirmPrefix
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_deploy_common.ps1')

if ($Execute -and $DryRun) { Write-Fail '-Execute and -DryRun/-WhatIf are mutually exclusive.'; exit 2 }
$doIt = [bool]$Execute
if (-not $Region) { Write-Fail '-Region is required.'; exit 2 }
try { Invoke-Helper -Arguments @('validate', '--prefix', $Prefix, '--region', $Region) | Out-Null } catch { Write-Fail $_.Exception.Message; exit 2 }
if ($Profile) { $env:AWS_PROFILE = $Profile }
$env:AWS_REGION = $Region; $env:AWS_DEFAULT_REGION = $Region

function New-TempJson($obj) {
    $f = Join-Path ([IO.Path]::GetTempPath()) "safepath-td-$([guid]::NewGuid().ToString('N')).json"
    ConvertTo-Json -InputObject $obj -Depth 8 -Compress | Set-Content -Path $f -Encoding ASCII
    return $f
}

# Delete every object version and delete marker in a bucket (batches of up to 1000).
function Clear-Bucket([string]$Bucket) {
    while ($true) {
        $json = Invoke-Aws -Arguments @('s3api', 'list-object-versions', '--bucket', $Bucket, '--max-items', '1000', '--output', 'json') -Region $Region
        $res = $json | ConvertFrom-Json
        $items = @()
        foreach ($v in @($res.Versions)) { if ($v) { $items += @{ Key = $v.Key; VersionId = $v.VersionId } } }
        foreach ($m in @($res.DeleteMarkers)) { if ($m) { $items += @{ Key = $m.Key; VersionId = $m.VersionId } } }
        if ($items.Count -eq 0) { break }
        $f = New-TempJson @{ Objects = $items; Quiet = $true }
        try { Invoke-Aws -Arguments @('s3api', 'delete-objects', '--bucket', $Bucket, '--delete', "file://$($f -replace '\\','/')") -Region $Region | Out-Null }
        finally { Remove-Item $f -ErrorAction SilentlyContinue }
        Write-Info "  deleted $($items.Count) object versions from $Bucket"
    }
}

Write-Host ''
if ($doIt) { Write-Host 'MODE: EXECUTE (deletes SafePath resources after confirmation)' -ForegroundColor Red }
else { Write-Host 'MODE: DRY RUN (nothing is deleted). Pass -Execute to tear down.' -ForegroundColor Green }
Write-Info "prefix=$Prefix region=$Region"

$tmpFiles = @()
try {
    Write-Step '1/3 Preflight'
    if (-not (Test-AwsCli)) {
        Write-Info 'aws CLI: NOT FOUND'
        Write-AwsCliInstructions
        if ($doIt) { Write-Fail 'Cannot execute without the aws CLI.'; exit 1 }
        Write-Host ''
        Write-Info 'Plan (what a real run would do, after listing live resources):'
        Write-Plan "list stacks in $Region; keep only names ${Prefix}-(data|api|web|observability|budget) tagged Project=SafePath"
        Write-Plan ("for each kept stack list its S3 buckets; keep only ${Prefix}-(graphs|photos|site|public-website)-* tagged Project=SafePath")
        Write-Plan 'print the list, require typing the prefix, empty the buckets (all versions), then: npx cdk destroy <stacks> --force'
        Write-Plan 'CDKToolkit (bootstrap) and anything else is never touched'
        Write-Host ''; Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Green
        exit 0
    }
    $id = Get-AwsIdentity -Region $Region
    Write-Info "account: $($id.Masked)   region: $Region"
    if ($id.IsRoot) { Write-Fail 'Caller is the account ROOT user; use a non-root identity.'; if ($doIt) { exit 1 } }
    $env:CDK_DEFAULT_ACCOUNT = $id.Account; $env:CDK_DEFAULT_REGION = $Region

    Write-Step '2/3 Plan (read-only discovery of live resources)'
    $live = @()
    $sj = Invoke-Aws -Arguments @('cloudformation', 'describe-stacks', '--output', 'json') -Region $Region | ConvertFrom-Json
    foreach ($s in @($sj.Stacks)) { $live += @{ StackName = $s.StackName; Tags = @($s.Tags) } }
    $f = New-TempJson $live; $tmpFiles += $f
    $sel = Invoke-Helper -Arguments @('select-stacks', '--prefix', $Prefix, '--stacks-file', $f)
    $stacks = @($sel.delete)
    foreach ($s in @($sel.skipped)) { if ($s -match "^$Prefix-") { Write-Warn "skipping $s" } }
    if ($stacks.Count -eq 0) { Write-Info 'No SafePath stacks found (tagged Project=SafePath, named with the prefix). Nothing to do.'; exit 0 }

    $buckets = @()
    foreach ($st in $stacks) {
        $rj = Invoke-Aws -Arguments @('cloudformation', 'list-stack-resources', '--stack-name', $st, '--output', 'json') -Region $Region | ConvertFrom-Json
        foreach ($r in @($rj.StackResourceSummaries)) {
            if ($r.ResourceType -ne 'AWS::S3::Bucket' -or -not $r.PhysicalResourceId) { continue }
            $b = $r.PhysicalResourceId
            try {
                $tj = Invoke-Aws -Arguments @('s3api', 'get-bucket-tagging', '--bucket', $b, '--output', 'json') -Region $Region | ConvertFrom-Json
                $tags = @($tj.TagSet)
            } catch { $tags = @() }
            $tf = New-TempJson $tags; $tmpFiles += $tf
            try {
                $chk = Invoke-Helper -Arguments @('check-bucket', '--prefix', $Prefix, '--name', $b, '--tags-file', $tf)
                $buckets += $b
            } catch { Write-Warn "NOT emptying bucket ${b}: $($_.Exception.Message)" }
        }
    }

    Write-Host ''
    Write-Host '   WILL BE DELETED' -ForegroundColor Yellow
    foreach ($b in $buckets) { Write-Info "  bucket (all object versions, then the bucket): $b" }
    foreach ($s in $stacks) { Write-Info "  stack: $s (DynamoDB tables, Lambdas, APIs, secrets, alarms, dashboards defined by it)" }
    Write-Info 'NOT touched: CDKToolkit and every other stack/bucket/resource. DynamoDB data is lost (no backups kept).'

    if (-not $doIt) { Write-Host ''; Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Green; exit 0 }

    Write-Step '3/3 Delete'
    if (-not (Confirm-Typed -Expected $Prefix -Prompt "Type the stack prefix '$Prefix' to DELETE the resources listed above" -Preset $ConfirmPrefix -Yes:$Yes)) {
        Write-Fail 'Confirmation did not match. Nothing was deleted.'; exit 1
    }
    foreach ($b in $buckets) { Write-Info "emptying $b"; Clear-Bucket $b }
    $disc = Read-Discovery
    $cf = Join-Path ([IO.Path]::GetTempPath()) "safepath-ctx-$([guid]::NewGuid().ToString('N')).json"; $tmpFiles += $cf
    $ctxArgs = @('context-args', '--discovery', $script:DiscoveryFile, '--prefix', $Prefix, '--region', $Region, '--no-require-package')
    ConvertTo-Json -InputObject @(Invoke-Helper -Arguments $ctxArgs) -Compress | Set-Content -Path $cf -Encoding ASCII
    $dcmd = @(Invoke-Helper -Arguments (@('destroy-cmd', '--prefix', $Prefix, '--ctx-file', $cf) + $stacks))
    Invoke-Step -Command $dcmd -Cwd $script:InfraDir -Execute $true
    Write-Host 'Teardown complete.' -ForegroundColor Green
    exit 0
}
catch { Write-Fail $_.Exception.Message; exit 1 }
finally { foreach ($t in $tmpFiles) { Remove-Item $t -ErrorAction SilentlyContinue } }
