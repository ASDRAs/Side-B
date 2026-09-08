param(
    [Parameter(Mandatory = $true)][string]$BackendImage,
    [Parameter(Mandatory = $true)][string]$InferenceImage
)

$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'cloudrun.json') -Raw | ConvertFrom-Json

function Invoke-Gcloud {
    param([string[]]$Arguments)
    & gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Cloud deployment command failed.' }
}

# 단위 테스트는 저장소 파일끼리만 비교하므로 배포 드리프트를 잡지 못한다.
# 트래픽을 옮기기 전에 실제 리비전 설정을 대조한다.
function Assert-Deployed {
    param([string]$Label, [string]$Expected, [string]$Actual)
    if ($Actual -ne $Expected) {
        throw "$Label mismatch: cloudrun.json says '$Expected', the deployed revision has '$Actual'."
    }
}

function Get-TrafficSignature {
    param($Service)
    $entries = @($Service.status.traffic | Where-Object { $_.percent -gt 0 } |
        ForEach-Object { "$($_.revisionName)=$($_.percent)" } | Sort-Object)
    if ($entries.Count -eq 0) { throw 'An existing production traffic allocation is required.' }
    return $entries -join ','
}

function Get-CandidateUrl {
    param($Service, [string]$Tag, [string]$Revision)
    $entries = @($Service.status.traffic | Where-Object { $_.tag -eq $Tag })
    if ($entries.Count -ne 1 -or $entries[0].revisionName -ne $Revision -or
        $entries[0].percent -gt 0 -or -not $entries[0].url) {
        throw 'Candidate tag changed or received production traffic. Recheck before promotion.'
    }
    return $entries[0].url
}

$scope = @('--project', $config.project, '--region', $config.region, '--quiet')
$revisionSuffix = 'clap-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$candidateName = "$($config.backend.service)-$revisionSuffix"
$inferenceRevisionName = "$($config.inference.service)-$revisionSuffix"
$backendBefore = ConvertFrom-Json (Invoke-Gcloud (@('run', 'services', 'describe', $config.backend.service,
    '--format=json') + $scope) | Out-String)
$inferenceBefore = ConvertFrom-Json (Invoke-Gcloud (@('run', 'services', 'describe', $config.inference.service,
    '--format=json') + $scope) | Out-String)
$backendTraffic = Get-TrafficSignature $backendBefore
$inferenceTraffic = Get-TrafficSignature $inferenceBefore
$inferenceAudience = $inferenceBefore.status.url
if (-not $inferenceAudience) { throw 'Inference service origin is missing.' }
$backendIdentity = Invoke-Gcloud (@('run', 'services', 'describe', $config.backend.service,
    '--format=value(spec.template.spec.serviceAccountName)') + $scope)
if (-not $backendIdentity) { throw 'An existing backend service identity is required.' }

# Keep inference private. Its runtime account is provisioned separately without project roles.
Invoke-Gcloud (@('run', 'deploy', $config.inference.service, '--image', $InferenceImage,
    '--revision-suffix', $revisionSuffix,
    '--no-traffic', '--tag', $revisionSuffix,
    '--service-account', $config.inference.service_account,
    '--cpu', [string]$config.inference.cpu, '--memory', $config.inference.memory,
    '--concurrency', [string]$config.inference.concurrency,
    '--min-instances', [string]$config.inference.min_instances,
    '--max-instances', [string]$config.inference.max_instances,
    '--timeout', [string]$config.inference.timeout_seconds,
    '--cpu-boost', '--no-allow-unauthenticated', '--invoker-iam-check',
    '--startup-probe', 'httpGet.path=/health,httpGet.port=8080,timeoutSeconds=3,periodSeconds=5,failureThreshold=36') + $scope)

Invoke-Gcloud (@('run', 'services', 'add-iam-policy-binding', $config.inference.service,
    '--member', "serviceAccount:$backendIdentity", '--role', 'roles/run.invoker') + $scope)
$inference = ConvertFrom-Json (Invoke-Gcloud (@('run', 'services', 'describe', $config.inference.service,
    '--format=json') + $scope) | Out-String)
Assert-Deployed 'Inference production traffic' $inferenceTraffic (Get-TrafficSignature $inference)
$inferenceUrl = Get-CandidateUrl $inference $revisionSuffix $inferenceRevisionName

# Stage the backend without moving production traffic or replacing existing secrets.
# The edge timeout must exceed the router's 150-second deadline.
$environment = "CLAP_INFERENCE_URL=$inferenceUrl,CLAP_INFERENCE_AUDIENCE=$inferenceAudience,CLAP_INFERENCE_USE_IAM=true,CLAP_INFERENCE_TIMEOUT_SECONDS=$($config.backend.inference_timeout_seconds)"
Invoke-Gcloud (@('run', 'deploy', $config.backend.service, '--image', $BackendImage,
    '--revision-suffix', $revisionSuffix,
    '--timeout', [string]$config.backend.timeout_seconds, '--update-env-vars', $environment,
    '--no-traffic', '--tag', $revisionSuffix) + $scope)
$backend = ConvertFrom-Json (Invoke-Gcloud (@('run', 'services', 'describe', $config.backend.service,
    '--format=json') + $scope) | Out-String)
Assert-Deployed 'Backend production traffic' $backendTraffic (Get-TrafficSignature $backend)
$backendUrl = Get-CandidateUrl $backend $revisionSuffix $candidateName
$candidate = ConvertFrom-Json (Invoke-Gcloud (@('run', 'revisions', 'describe', $candidateName,
    '--format=json') + $scope) | Out-String)
$deployedEnvironment = @{}
foreach ($entry in $candidate.spec.containers[0].env) { $deployedEnvironment[$entry.name] = $entry.value }
Assert-Deployed 'Backend request timeout' $config.backend.timeout_seconds $candidate.spec.timeoutSeconds
Assert-Deployed 'CLAP_INFERENCE_URL' $inferenceUrl $deployedEnvironment['CLAP_INFERENCE_URL']
Assert-Deployed 'CLAP_INFERENCE_AUDIENCE' $inferenceAudience $deployedEnvironment['CLAP_INFERENCE_AUDIENCE']
Assert-Deployed 'CLAP_INFERENCE_USE_IAM' 'true' $deployedEnvironment['CLAP_INFERENCE_USE_IAM']
Assert-Deployed 'CLAP_INFERENCE_TIMEOUT_SECONDS' $config.backend.inference_timeout_seconds $deployedEnvironment['CLAP_INFERENCE_TIMEOUT_SECONDS']

$inference = ConvertFrom-Json (Invoke-Gcloud (@('run', 'services', 'describe', $config.inference.service,
    '--format=json') + $scope) | Out-String)
Assert-Deployed 'Inference production traffic' $inferenceTraffic (Get-TrafficSignature $inference)
Assert-Deployed 'Inference candidate URL' $inferenceUrl (Get-CandidateUrl $inference $revisionSuffix $inferenceRevisionName)
$inferenceRevision = ConvertFrom-Json (Invoke-Gcloud (@('run', 'revisions', 'describe', $inferenceRevisionName,
    '--format=json') + $scope) | Out-String)
Assert-Deployed 'Inference request timeout' $config.inference.timeout_seconds $inferenceRevision.spec.timeoutSeconds
Assert-Deployed 'Inference concurrency' $config.inference.concurrency $inferenceRevision.spec.containerConcurrency
Assert-Deployed 'Inference max instances' $config.inference.max_instances $inferenceRevision.metadata.annotations.'autoscaling.knative.dev/maxScale'

Write-Output "Candidate $candidateName staged and its deployed settings match cloudrun.json."
Write-Output "Test backend candidate: $backendUrl"
Write-Output "Pinned inference revision: $inferenceRevisionName ($inferenceUrl)"
Write-Output "Previous backend traffic: $backendTraffic"
Write-Output "Unchanged inference traffic: $inferenceTraffic"
Write-Output 'After verification, promote only the backend revision. Keep its inference tag unchanged for its entire serving and rollback lifetime.'
Write-Output 'Rollback restores previous backend traffic; do not move inference production traffic or delete tags referenced by retained backend revisions.'
