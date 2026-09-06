param([string]$Scenario = 'ok')
$ErrorActionPreference = 'Stop'
$global:fixtureConfig = Get-Content (Join-Path $PSScriptRoot '../cloudrun.json') -Raw | ConvertFrom-Json
$global:revisionNames = @{}
$global:candidateTags = @{}
$global:backendEnvironment = @{}

# Shadow the CLI completely: this test must never contact Google Cloud.
function gcloud {
    $global:LASTEXITCODE = 0
    $a = @($args)
    $cfg = $global:fixtureConfig
    if ($a[1] -eq 'deploy') {
        if ($a -notcontains '--no-traffic') { throw 'Every deployment must preserve production traffic' }
        $suffix = $a[[array]::IndexOf($a, '--revision-suffix') + 1]
        if ($suffix -notmatch '^clap-[a-f0-9]{12}$') { throw 'Missing unique revision suffix' }
        $global:revisionNames[$a[2]] = "$($a[2])-$suffix"
        $tag = $a[[array]::IndexOf($a, '--tag') + 1]
        if ($tag -ne $suffix) { throw 'Candidate tag must be unique for each deployment' }
        $global:candidateTags[$a[2]] = $tag
        if ($a[2] -eq $cfg.backend.service) {
            $environment = $a[[array]::IndexOf($a, '--update-env-vars') + 1]
            foreach ($entry in $environment.Split(',')) {
                $parts = $entry.Split('=', 2)
                $global:backendEnvironment[$parts[0]] = $parts[1]
            }
            if ($global:backendEnvironment['CLAP_INFERENCE_URL'] -ne "https://$tag---inference.example.com" -or
                $global:backendEnvironment['CLAP_INFERENCE_AUDIENCE'] -ne 'https://inference.example.com') {
                throw 'Backend must route to the tagged revision using the untagged IAM audience'
            }
        }
        return
    }
    if ($a[2] -eq 'add-iam-policy-binding') { return }
    if ($a -contains '--format=value(spec.template.spec.serviceAccountName)') { return 'fixture@example.com' }
    if ($a -contains '--format=value(status.url)') { return 'https://inference.example.com' }
    if ($a[1] -eq 'services' -and $a[2] -eq 'describe' -and $a -contains '--format=json') {
        $service = $a[3]
        $name = $global:revisionNames[$service]
        $traffic = @(@{ revisionName = "$service-old"; percent = 100 })
        $origin = if ($service -eq $cfg.backend.service) { 'backend.example.com' } else { 'inference.example.com' }
        if ($name) {
            $tag = $global:candidateTags[$service]
            $traffic += @{ revisionName = $name; tag = $tag; url = "https://$tag---$origin"; percent = 0 }
            if ($Scenario -eq 'wrong-tag') { $traffic[1].revisionName = 'another-deployment' }
            if ($Scenario -eq 'split-traffic') {
                $traffic[0].percent = 50
                $traffic[1].percent = 50
            }
        }
        # A competing revision can be latest without being our candidate or serving traffic.
        return (@{ status = @{ url = "https://$origin"; latestCreatedRevisionName = 'concurrent-revision'; traffic = $traffic } } | ConvertTo-Json -Depth 10)
    }
    if ($a[1] -eq 'revisions' -and $a[2] -eq 'describe') {
        $name = $a[3]
        if ($name -eq $global:revisionNames[$cfg.backend.service]) {
            return (@{ spec = @{
                timeoutSeconds = $cfg.backend.timeout_seconds
                containers = @(@{ env = @(
                    @{ name = 'CLAP_INFERENCE_URL'; value = $global:backendEnvironment['CLAP_INFERENCE_URL'] },
                    @{ name = 'CLAP_INFERENCE_AUDIENCE'; value = $global:backendEnvironment['CLAP_INFERENCE_AUDIENCE'] },
                    @{ name = 'CLAP_INFERENCE_USE_IAM'; value = 'true' },
                    @{ name = 'CLAP_INFERENCE_TIMEOUT_SECONDS'; value = [string]$cfg.backend.inference_timeout_seconds }
                ) })
            } } | ConvertTo-Json -Depth 10)
        }
        if ($name -ne $global:revisionNames[$cfg.inference.service]) { throw "Wrong revision inspected: $name" }
        $timeout = $cfg.inference.timeout_seconds
        if ($Scenario -eq 'timeout-drift') { $timeout = 60 }
        return (@{
            spec = @{ timeoutSeconds = $timeout; containerConcurrency = $cfg.inference.concurrency }
            metadata = @{ annotations = @{ 'autoscaling.knative.dev/maxScale' = [string]$cfg.inference.max_instances } }
        } | ConvertTo-Json -Depth 10)
    }
    throw "Unexpected CLI call: $a"
}

& (Join-Path $PSScriptRoot '../deploy-clap.ps1') -BackendImage fixture-backend -InferenceImage fixture-inference
