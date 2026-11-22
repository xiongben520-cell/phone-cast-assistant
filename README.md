# 手机投屏助手（Phone Cast Assistant）

把 Android 手机（已在小米 13 Ultra / Windows 上实测）投屏到 Windows 的**开箱即用**工具：
一个带 GUI 的启动器，把 `adb` 与 `scrcpy` 全部打包，解压后双击即可使用，无需安装 Python、adb 或 scrcpy。

支持 **USB 有线** 与 **Wi-Fi 无线**（配对码 / 扫码）两种连接方式，视频码率按实测链路带宽的 80% 动态决定，帧率固定 60。

## 功能

- 有线 USB / 无线 Wi-Fi 自动识别与一键连接；新手机可用 6 位配对码或二维码扫码配对
- 连接时自动测带宽，按 80% 决定码率（USB 上限 300 Mbps，无线上限 30 Mbps），固定 60 fps
- 音频整路转发到电脑，手机侧静音；连接成功后自动播放一声测试音
- 手机亮屏 / 黑屏切换（只关显示面板、继续投屏、不锁屏）
- 快捷键贴图（半透明小抄），可折叠、可开关跟随、标题栏带置顶按钮
- 投屏窗口置顶开关；横竖屏切换时按中心点重定位，避免窗口跳太远
- 连接时**不改动**手机的音量与静音状态

## 快速开始

1. 到 [Releases](../../releases) 下载 `phone-cast-assistant_v1.5.0.zip`
2. 解压到任意目录（路径不要放在需要管理员权限的位置）
3. 双击 `app/手机投屏助手.exe`
4. 手机开启「USB 调试」并用数据线连接，或开启「无线调试」完成一次配对；点击「刷新」→「连接并投屏」

完整说明见 [docs/USAGE.md](docs/USAGE.md)。

> **小米用户注意**：若锁屏 / 支付密码界面在投屏里看不到或点不动，需在手机「开发者选项」中打开
> 「USB 调试（安全设置）」。这是系统对模拟点击安全界面的限制，PC 端无法绕过。

## 从源码构建

需要 Windows + Python 3.11，并把 `adb.exe` 与 `scrcpy`（Windows 版）放到 `tools/` 下：

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

构建产物在 `dist/手机投屏助手/`。更详细的参数见 [scripts/build.ps1](scripts/build.ps1)。

## 目录结构

```
src/
  app_main.py       GUI 主程序：设备发现、配对、带宽实测、连接流程、窗口控制
  overlay_main.py   快捷键贴图进程（与主程序通过 overlay_cmd.txt / overlay_status.txt 通信）
  qr_locator.py     二维码定位解码（把 adb pair --qr 的终端字符还原成二维码）
  win32util.py      Win32 窗口工具：找 scrcpy 窗口、置顶、重定位、发送黑屏/亮屏快捷键
docs/USAGE.md       面向最终用户的使用说明
scripts/build.ps1   PyInstaller 打包脚本
```

## 已知限制

- 仅支持 Windows（依赖 Win32 API 与 PyInstaller onedir 打包）
- 快捷键贴图进程会在 scrcpy 窗口消失后自动隐藏
- 视频码率上限受手机编码器能力约束；实测 1440p60 / 273 Mbps 可稳定输出

## 许可证

[MIT](LICENSE)。`tools/` 中的 adb / scrcpy 为各自上游项目所有，不随本仓库分发，请从官方渠道获取。
