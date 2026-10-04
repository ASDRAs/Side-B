$ErrorActionPreference = 'Stop'

$extensionRoot = $PSScriptRoot
$repositoryRoot = Split-Path -Parent $extensionRoot
$manifest = Get-Content -LiteralPath (Join-Path $extensionRoot 'manifest.json') -Raw | ConvertFrom-Json
$package = Get-Content -LiteralPath (Join-Path $extensionRoot 'package.json') -Raw | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "manifest.json has an invalid release version: $version"
}
if ([string]$package.version -ne $version) {
    throw "manifest.json and package.json versions do not match"
}

$releaseRoot = Join-Path $repositoryRoot 'outputs/releases'
$packageName = "Side-B-$version"
$stagingRoot = Join-Path $releaseRoot $packageName
$archivePath = Join-Path $releaseRoot "$packageName.zip"
$resolvedReleaseRoot = [System.IO.Path]::GetFullPath($releaseRoot)
$resolvedStagingRoot = [System.IO.Path]::GetFullPath($stagingRoot)
if (-not $resolvedStagingRoot.StartsWith($resolvedReleaseRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to stage outside the release directory: $resolvedStagingRoot"
}

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
New-Item -ItemType Directory -Path $stagingRoot | Out-Null

$files = @(
    'auth.config.js',
    'background.js',
    'manifest.json',
    'offscreen.html',
    'offscreen.js',
    'README.md',
    'sidepanel.css',
    'sidepanel.html',
    'sidepanel.js'
)
$directories = @('dist', 'fonts', 'icons', 'scripts')

foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $extensionRoot $file) -Destination $stagingRoot
}
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'LICENSE') -Destination $stagingRoot
foreach ($directory in $directories) {
    Copy-Item -LiteralPath (Join-Path $extensionRoot $directory) -Destination $stagingRoot -Recurse
}
Remove-Item -LiteralPath (Join-Path $stagingRoot 'scripts/authWorker.entry.js') -Force

Compress-Archive -LiteralPath $stagingRoot -DestinationPath $archivePath -CompressionLevel Optimal
Remove-Item -LiteralPath $stagingRoot -Recurse -Force

$archive = Get-Item -LiteralPath $archivePath
Write-Output "Created $($archive.FullName) ($($archive.Length) bytes)"
