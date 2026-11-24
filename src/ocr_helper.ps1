# Windows 原生中文 OCR（Windows.Media.Ocr）—— 零第三方依赖
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File ocr_helper.ps1 <图片路径> <结果文件路径>
# 结果以 UTF-8 无 BOM 写入第二个参数指定的文件；调用方自己读文件，避免 PowerShell 控制台编码问题。
param(
    [Parameter(Mandatory=$true)][string]$ImagePath,
    [Parameter(Mandatory=$true)][string]$ResultPath
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

# PS 5.1 对 WinRT 异步操作的泛型推断失败，用反射显式 MakeGenericMethod
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
} | Select-Object -First 1

function Await-Op($op, [type]$t) {
    $gm = $asTask.MakeGenericMethod($t)
    $task = $gm.Invoke($null, @($op))
    return $task.GetAwaiter().GetResult()
}

try {
    $file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream = Await-Op ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Op ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new("zh-Hans-CN"))
    if ($null -eq $engine) { throw "系统未安装中文 OCR 语言包 (zh-Hans-CN)" }
    $result = Await-Op ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    [System.IO.File]::WriteAllText($ResultPath, $result.Text, [System.Text.UTF8Encoding]::new($false))
    Write-Output "OK"
} catch {
    [System.IO.File]::WriteAllText($ResultPath, "ERR:" + $_.Exception.Message, [System.Text.UTF8Encoding]::new($false))
    Write-Output "ERR"
}
