$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

. ".\.venv\Scripts\Activate.ps1"
$version = python.exe -c "from config.version import APP_VERSION; print(APP_VERSION)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($version -notmatch '^\d+\.\d+(?:\.\d+)?$') { throw "Invalid APP_VERSION: $version" }
$name = "easycom-v$version"

$oneFileChoice = Read-Host "是否打包成 onefile？输入 y 确认，直接回车默认否"
$oneFile = $oneFileChoice.Trim().ToLowerInvariant() -in @("y", "yes", "1")
$packageMode = if ($oneFile) { "onefile" } else { "onedir" }
Write-Host "[INFO] Packaging mode: $packageMode"

$distDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path "dist"))
$outputPath = if ($oneFile) {
    [System.IO.Path]::GetFullPath((Join-Path $distDir "$name.exe"))
} else {
    [System.IO.Path]::GetFullPath((Join-Path $distDir $name))
}
$zipPath = [System.IO.Path]::GetFullPath((Join-Path $distDir "$name.zip"))
if ([System.IO.Path]::GetDirectoryName($outputPath) -ne $distDir -or
    [System.IO.Path]::GetDirectoryName($zipPath) -ne $distDir) {
    throw "Release output must stay inside dist."
}
if (Test-Path -LiteralPath $distDir) {
    $distItem = Get-Item -LiteralPath $distDir -Force
    if ($distItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to clean a linked dist directory: $distDir"
    }
}
$exePath = if ($oneFile) { $outputPath } else { Join-Path $outputPath "$name.exe" }
$runningRelease = Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [System.IO.Path]::GetFullPath($_.Path) -eq $exePath
}
if ($runningRelease) {
    throw "Close the running release before rebuilding: $exePath"
}
if (Test-Path -LiteralPath $outputPath) {
    $outputItem = Get-Item -LiteralPath $outputPath -Force
    if ($outputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to clean a linked release output: $outputPath"
    }
    Remove-Item -LiteralPath $outputPath -Recurse -Force
    Write-Host "[INFO] Removed previous release output: $outputPath"
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
    Write-Host "[INFO] Removed previous release ZIP: $zipPath"
}

$buildArgs = @(
    ".\scripts\build_exe.py",
    "--name", $name
)
if ($oneFile) { $buildArgs += "--onefile" }

python.exe @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path -LiteralPath $outputPath)) {
    throw "Expected release output was not created: $outputPath"
}
Compress-Archive -LiteralPath $outputPath -DestinationPath $zipPath -Force
Write-Host "[OK] ZIP: $zipPath"
