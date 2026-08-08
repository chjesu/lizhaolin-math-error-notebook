param(
    [ValidateSet('doctor', 'start', 'stop', 'status', 'run')]
    [string]$Action = 'status',
    [switch]$IncludeExisting,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot 'config\exam-ingest-watcher.json'
$servicePath = Join-Path $projectRoot 'services\exam_ingest_watcher.py'
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$python = [string]$config.python
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Configured Python was not found: $python"
}

$arguments = @('-X', 'utf8', '-B', $servicePath, '--config', $configPath, $Action)
if ($IncludeExisting) {
    $arguments += '--include-existing'
}
if ($Once) {
    if ($Action -ne 'run') {
        throw '-Once can only be used with -Action run.'
    }
    $arguments += '--once'
}

& $python @arguments
exit $LASTEXITCODE
