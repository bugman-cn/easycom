$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
. ".\.venv\Scripts\Activate.ps1"

$choice = Read-Host "是否打包成 onefile？输入 y 确认，直接回车生成目录版"
$buildArgs = @(".\scripts\build_exe.py", "--release")
if ($choice.Trim().ToLowerInvariant() -in @("y", "yes", "1")) {
    $buildArgs += "--onefile"
}
python.exe @buildArgs
exit $LASTEXITCODE
