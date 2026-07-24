$ErrorActionPreference = "Stop"

# Codex/CI launchers can inject both `Path` and `PATH`.  Windows treats them
# case-insensitively, while Start-Process rejects the duplicate environment map.
$processPath = $env:Path
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
[Environment]::SetEnvironmentVariable("Path", $processPath, "Process")

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$node = "C:\Program Files\nodejs\node.exe"
$nextCli = Join-Path $frontendRoot "node_modules\next\dist\bin\next"
$logRoot = Join-Path $env:TEMP "localfit-dev"

$occupied = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(3000, 8000) }
$occupiedPorts = @($occupied | Select-Object -ExpandProperty LocalPort -Unique)

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$env:PYTHONPYCACHEPREFIX = Join-Path $logRoot "pycache"

if (8000 -notin $occupiedPorts) {
    # Direct backend redirection prevents uvicorn's Windows reload signal from completing.
    Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--reload") `
        -WorkingDirectory $backendRoot `
        -WindowStyle Hidden
} else {
    Write-Host "Backend already listening on port 8000."
}

if (3000 -notin $occupiedPorts) {
    foreach ($name in @("frontend.out.log", "frontend.err.log")) {
        $path = Join-Path $logRoot $name
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
    # Launch Next directly so the reload process is not tied to npm.cmd's wrapper lifecycle.
    Start-Process `
        -FilePath $node `
        -ArgumentList @($nextCli, "dev", "--hostname", "127.0.0.1", "--port", "3000") `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "frontend.out.log") `
        -RedirectStandardError (Join-Path $logRoot "frontend.err.log")
} else {
    Write-Host "Frontend already listening on port 3000."
}

Write-Host "Frontend: http://127.0.0.1:3000"
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend logs: $logRoot"
Write-Host "Pipeline logs: $(Join-Path $projectRoot 'runtime\admin\logs')"
