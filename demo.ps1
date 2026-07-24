param(
    [switch]$NoBrowser,
    [switch]$CheckOnly,
    [string]$PythonPath = $env:LOCALFIT_DEMO_PYTHON
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $repoRoot "final_proj\backend"
$frontendDir = Join-Path $repoRoot "final_proj\frontend"
$demoRuntimeDir = Join-Path $repoRoot ".demo"
$venvDir = Join-Path $demoRuntimeDir "venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$backendOut = Join-Path $demoRuntimeDir "backend.out.log"
$backendErr = Join-Path $demoRuntimeDir "backend.err.log"
$frontendOut = Join-Path $demoRuntimeDir "frontend.out.log"
$frontendErr = Join-Path $demoRuntimeDir "frontend.err.log"
$backendProcess = $null
$frontendProcess = $null

function Write-Step([string]$Message) {
    Write-Host "`n[LocalFit Lab] $Message" -ForegroundColor Cyan
}

function Wait-Http([string]$Url, [string]$Label, [int]$TimeoutSeconds = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 4
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "  OK  $Label" -ForegroundColor Green
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 750
        }
    }
    throw "$Label did not become ready: $Url"
}

function Stop-ProcessTree($Process) {
    if ($null -eq $Process) { return }
    try {
        if (-not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
        }
    }
    catch {
        # The process may already have exited.
    }
}

try {
    Write-Step "실행 데모 준비"
    New-Item -ItemType Directory -Force -Path $demoRuntimeDir | Out-Null

    if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
        throw "Node.js 20 이상이 필요합니다: https://nodejs.org/"
    }
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        throw "npm을 찾지 못했습니다. Node.js LTS를 설치해 주세요."
    }

    if (-not (Test-Path -LiteralPath $pythonExe)) {
        $pythonCandidates = @()
        if ($PythonPath) {
            $pythonCandidates += [pscustomobject]@{ File = $PythonPath; Prefix = @() }
        }
        $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pythonLauncher) {
            $pythonCandidates += [pscustomobject]@{ File = $pythonLauncher.Source; Prefix = @("-3") }
        }
        $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($systemPython) {
            $pythonCandidates += [pscustomobject]@{ File = $systemPython.Source; Prefix = @() }
        }

        $selectedPython = $null
        foreach ($candidate in $pythonCandidates) {
            try {
                $prefixArguments = @($candidate.Prefix)
                & $candidate.File $prefixArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $selectedPython = $candidate
                    break
                }
            }
            catch {
                continue
            }
        }
        if (-not $selectedPython) {
            throw "Python 3.11 이상이 필요합니다: https://www.python.org/downloads/"
        }
        $selectedPrefixArguments = @($selectedPython.Prefix)
        & $selectedPython.File $selectedPrefixArguments -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Python 가상환경 생성에 실패했습니다." }
    }

    Write-Step "데모 백엔드 의존성 확인"
    & $pythonExe -m pip install --disable-pip-version-check -q -r (Join-Path $backendDir "requirements-demo.txt")
    if ($LASTEXITCODE -ne 0) { throw "Python 패키지 설치에 실패했습니다." }

    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules"))) {
        Write-Step "프론트엔드 패키지 설치 (최초 한 번)"
        Push-Location $frontendDir
        try {
            & $npmCommand.Source ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci에 실패했습니다." }
        }
        finally {
            Pop-Location
        }
    }

    Write-Step "데모 서버 시작"
    $backendProcess = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList @("-m", "uvicorn", "demo_main:app", "--host", "127.0.0.1", "--port", "4311") `
        -WorkingDirectory $backendDir `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -WindowStyle Hidden `
        -PassThru

    $frontendCommand = "set NEXT_PUBLIC_DEMO_MODE=true&& set NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:4311&& npm run dev -- --hostname 127.0.0.1 --port 4310"
    $frontendProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList @("/d", "/s", "/c", $frontendCommand) `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -WindowStyle Hidden `
        -PassThru

    Wait-Http "http://127.0.0.1:4311/healthz" "데모 API"
    Wait-Http "http://127.0.0.1:4310" "데모 웹"

    Write-Host "`n실행 데모가 준비되었습니다." -ForegroundColor Green
    Write-Host "  Web  http://127.0.0.1:4310"
    Write-Host "  API  http://127.0.0.1:4311/docs"
    Write-Host "  Data 합성 샘플 데이터 (운영 DB와 분리)"
    Write-Host "  Logs $demoRuntimeDir"

    if (-not $NoBrowser -and -not $CheckOnly) {
        Start-Process "http://127.0.0.1:4310"
    }

    if (-not $CheckOnly) {
        Write-Host "`n이 창에서 Enter를 누르면 데모 서버를 종료합니다."
        Read-Host | Out-Null
    }
}
catch {
    Write-Host "`n실행 데모 시작 실패: $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path -LiteralPath $backendErr) {
        Write-Host "`nBackend log:" -ForegroundColor Yellow
        Get-Content -LiteralPath $backendErr -Tail 20
    }
    if (Test-Path -LiteralPath $frontendErr) {
        Write-Host "`nFrontend log:" -ForegroundColor Yellow
        Get-Content -LiteralPath $frontendErr -Tail 20
    }
    exit 1
}
finally {
    Stop-ProcessTree $frontendProcess
    Stop-ProcessTree $backendProcess
}
