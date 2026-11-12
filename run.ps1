$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Stage([string]$title) {
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host "[阶段] $title" -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor Cyan
}

function Write-Complete([string]$title, [System.Diagnostics.Stopwatch]$watch) {
    Write-Host "[完成] $title，耗时 $([Math]::Round($watch.Elapsed.TotalSeconds, 2))s" -ForegroundColor Green
}

function Invoke-SetupEnv {
    & python.exe .\scripts\setup_env.py | ForEach-Object {
        $color = switch -Regex ($_){
            "^(=|\[阶段\])" { "Cyan"; break }
            "^\[(完成|就绪|成功|总耗时)\]" { "Green"; break }
            "^\[(信息|操作)\]" { "Yellow"; break }
            "^\[失败\]" { "Red"; break }
            default { "Gray" }
        }
        Write-Host $_ -ForegroundColor $color
    }
    return $LASTEXITCODE
}

$total = [System.Diagnostics.Stopwatch]::StartNew()
$exitCode = 1
try {
    Write-Stage "准备 Python 环境"
    $stage = [System.Diagnostics.Stopwatch]::StartNew()
    $setupExitCode = Invoke-SetupEnv
    Write-Complete "准备 Python 环境" $stage
    if ($setupExitCode -ne 0) { throw "环境部署失败，退出码: $setupExitCode" }

    Write-Stage "启动 easycom"
    $stage = [System.Diagnostics.Stopwatch]::StartNew()
    . "$PSScriptRoot\.venv\Scripts\Activate.ps1"
    python.exe .\main.py
    Write-Complete "启动 easycom" $stage
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Host "[失败] $_" -ForegroundColor Red
}
finally {
    Write-Host ("=" * 64) -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
    Write-Host "[总耗时] $([Math]::Round($total.Elapsed.TotalSeconds, 2))s" -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
    Write-Host ("=" * 64) -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
}
exit $exitCode
