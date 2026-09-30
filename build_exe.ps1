# 打包 Windows 单文件 exe（与 Releases 里那个 DanmakuEditor.exe 同样的做法）
#
#   pwsh -File build_exe.ps1
#
# 产物：dist\DanmakuEditor.exe（单文件、双击直接进 GUI，不带控制台窗口）
# 需要：python 3.8+ 与 pyinstaller（pip install pyinstaller）

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    Write-Host '>>> 清理旧产物'
    Remove-Item build, dist, DanmakuEditor.spec -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host '>>> pyinstaller 开始打包'
    python -m PyInstaller `
        --noconfirm --clean --onefile --windowed `
        --name DanmakuEditor `
        --collect-data customtkinter `
        --exclude-module numpy --exclude-module PIL --exclude-module pytest `
        --exclude-module matplotlib --exclude-module unittest `
        danmaku_editor.py
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller 失败，退出码 $LASTEXITCODE" }

    Write-Host '>>> 清理中间目录'
    Remove-Item build, DanmakuEditor.spec -Recurse -Force -ErrorAction SilentlyContinue

    $exe = Join-Path $root 'dist\DanmakuEditor.exe'
    if (-not (Test-Path $exe)) { throw "没找到产物 $exe" }
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ">>> 完成: $exe  ($mb MB)"
} finally {
    Pop-Location
}
