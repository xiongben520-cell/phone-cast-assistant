# 手机投屏助手 —— Windows 打包脚本
# 用法（在仓库根目录执行）:
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#
# 前置条件:
#   1) Python 3.11 已安装且在 PATH 中（或用 -Python 指定解释器路径）
#   2) 仓库根目录下有 tools\adb.exe 与 tools\scrcpy\scrcpy.exe
#      （adb 来自 Android Platform Tools，scrcpy 来自 Genymobile/scrcpy 官方 Release）
param(
    [string]$Python = "python",
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"
Push-Location $Root
try {
    $py = (Get-Command $Python -ErrorAction Stop).Source

    if (-not (Test-Path "tools\adb.exe")) {
        throw "缺少 tools\adb.exe —— 请先放置 Android Platform Tools 的 adb.exe"
    }
    if (-not (Test-Path "tools\scrcpy\scrcpy.exe")) {
        throw "缺少 tools\scrcpy\scrcpy.exe —— 请先放置 scrcpy Windows 版"
    }

    & $py -m PyInstaller --noconfirm --onedir --noconsole `
        --name "手机投屏助手" --specpath . `
        --add-data "src\overlay_main.py;." `
        --add-data "src\ocr_helper.ps1;." `
        --hidden-import win32util --hidden-import qr_locator --hidden-import agent_bridge --hidden-import ad_skip `
        --collect-all qrcode `
        --collect-all pycaw --collect-all comtypes `
        --hidden-import cv2 --hidden-import numpy `
        --hidden-import PIL.ImageTk --hidden-import PIL._tkinter_finder `
        (Join-Path $Root "src\app_main.py")

    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }

    # 组装 release 目录：exe + tools + 使用说明
    $app = "release\手机投屏助手\app"
    if (Test-Path $app) { Remove-Item $app -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    Copy-Item "dist\手机投屏助手\*" $app -Recurse -Force
    Copy-Item "tools" (Join-Path $app "tools") -Recurse -Force
    Copy-Item "src\ocr_helper.ps1" (Join-Path $app "ocr_helper.ps1") -Force
    Copy-Item "docs\USAGE.md" "release\手机投屏助手\使用说明.md" -Force

    Write-Host "构建完成: $Root\release\手机投屏助手"
}
finally {
    Pop-Location
}
