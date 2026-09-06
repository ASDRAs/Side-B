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

$scope = @('--project', $config.project, '--region', $config.region, '--quiet')
$backendIdentity = Invoke-Gcloud (@('run', 'services', 'describe', $config.backend.service,
    '--format=value(spec.template.spec.serviceAccountName)') + $scope)
if (-not $backendIdentity) { throw 'An existing backend service identity is required.' }

# Keep inference private. Its runtime account is provisioned separately without project roles.
Invoke-Gcloud (@('run', 'deploy', $config.inference.service, '--image', $InferenceImage,
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
$inferenceUrl = Invoke-Gcloud (@('run', 'services', 'describe', $config.inference.service,
    '--format=value(status.url)') + $scope)
if (-not $inferenceUrl) { throw 'Inference service URL is missing.' }

# Stage the backend without moving production traffic or replacing existing secrets.
# The edge timeout must exceed the router's 150-second deadline.
$environment = "CLAP_INFERENCE_URL=$inferenceUrl,CLAP_INFERENCE_USE_IAM=true,CLAP_INFERENCE_TIMEOUT_SECONDS=$($config.backend.inference_timeout_seconds)"
Invoke-Gcloud (@('run', 'deploy', $config.backend.service, '--image', $BackendImage,
    '--timeout', [string]$config.backend.timeout_seconds, '--update-env-vars', $environment,
    '--no-traffic', '--tag', 'clap-candidate') + $scope)
Write-Output 'Candidate staged. Test the tagged backend URL before explicitly promoting its revision.'
