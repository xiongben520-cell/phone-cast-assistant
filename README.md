# 手机投屏助手（Phone Cast Assistant）

把 Android 手机（已在小米 13 Ultra / Windows 上实测）投屏到 Windows 的**开箱即用**工具：
一个带 GUI 的启动器，把 `adb` 与 `scrcpy` 全部打包，解压后双击即可使用，无需安装 Python、adb 或 scrcpy。

支持 **USB 有线** 与 **Wi-Fi 无线**（配对码 / 扫码）两种连接方式，视频码率按实测链路带宽的 80% 动态决定，帧率固定 60。

它同时也是**电脑上的 AI Agent 操作手机的通道**：GUI 只负责把连接建立好，之后 Agent 复用同一条
adb / scrcpy 链路即可「看屏幕 + 点屏幕 + 输入文字」，人可以一边看投屏一边随时接管。

## 功能

- 有线 USB / 无线 Wi-Fi 自动识别与一键连接；新手机可用 6 位配对码或二维码扫码配对
- 连接时自动测带宽，按 80% 决定码率（USB 上限 300 Mbps，无线上限 30 Mbps），固定 60 fps
- 音频整路转发到电脑，手机侧静音；连接成功后自动播放一声测试音
- 手机亮屏 / 黑屏切换（只关显示面板、继续投屏、不锁屏）
- 快捷键贴图（半透明小抄），可折叠、可开关跟随、标题栏带置顶按钮
- 投屏窗口置顶开关；横竖屏切换时按中心点重定位，避免窗口跳太远
- 连接时**不改动**手机的音量与静音状态

## 🤖 给 AI Agent 用：让电脑上的 Agent 控制手机

这是本项目最实用的能力之一：**GUI 负责连接，Agent 负责操作**。两者共用同一条 adb 通道，互不冲突。

### 工作原理

```
电脑上的 Agent（OpenCode / Claude / 自研程序）
        │
        ├─ adb shell input ...        → 点击、滑动、按键、输入文字
        ├─ adb exec-out screencap     → 截图，交给视觉模型理解当前界面
        ├─ adb shell uiautomator dump → 导出控件树（文字 + 坐标），比截图更适合定位
        │
        └─ 投屏窗口（scrcpy）          → 人实时看到 Agent 在做什么，可随时鼠标接管
```

GUI 连接成功后，`tools/adb.exe` 就在 exe 同目录，Agent 不需要自己装任何东西，
直接用它向同一个串号发命令即可（串号可从 GUI「已发现设备」下拉框复制）。

### Agent 常用操作（把 `<serial>` 换成设备串号）

```powershell
$adb = ".\tools\adb.exe"
$s   = "<serial>"

# 1) 看屏幕：截图（交给视觉模型）
& $adb -s $s exec-out screencap -p > screen.png

# 2) 看控件：导出 UI 层级，拿到文字和 bbox 坐标（推荐，比纯截图精准）
& $adb -s $s shell uiautomator dump /sdcard/window.xml
& $adb -s $s pull /sdcard/window.xml window.xml

# 3) 点击坐标 / 滑动
& $adb -s $s shell input tap 720 1600
& $adb -s $s shell input swipe 700 2200 700 900 300

# 4) 输入文字 / 按键
& $adb -s $s shell input text "hello"
& $adb -s $s shell input keyevent 4      # BACK
& $adb -s $s shell input keyevent 3      # HOME

# 5) 打开 App
& $adb -s $s shell monkey -p com.example.app -c android.intent.category.LAUNCHER 1
```

### 典型 Agent 工作流

1. `uiautomator dump` 或 `screencap` 获取当前界面
2. 让模型判断下一步操作（点哪个按钮 / 输入什么）
3. `input tap` / `input text` 执行
4. 回到第 1 步，直到任务完成；人通过投屏窗口全程可见

### 为什么比纯 adb 方案好用

- **人机协同**：投屏窗口让人实时看到 Agent 的操作，出错了立刻用鼠标接管；快捷键贴图提供返回 / Home / 音量等一键操作
- **连接稳定**：GUI 已处理好配对、mDNS 重连、USB 授权检测、蓝牙冲突、音频路由这些坑
- **画质自适应**：每次连接按实测带宽定码率，Agent 看屏幕不糊；60 fps 固定
- **不抢焦点**：投屏窗口可置顶，也可关闭置顶让 Agent 在后台操作、人干别的

### 注意事项

- 小米手机必须打开「开发者选项 → USB 调试（安全设置）」，否则系统禁止模拟点击锁屏 / 支付密码等安全界面
- 有些 App 的控件树会被混淆或屏蔽，此时退回到截图 + 坐标点击
- `input text` 对中文支持不好，中文建议用剪贴板方案或 ADBKeyboard
- 投屏时手机默认保持亮屏；需要息屏省电可用 GUI 的「手机：黑屏」按钮（但要操作安全界面时请先切回亮屏）

## 快速开始

1. 到 [Releases](../../releases) 下载 `phone-cast-assistant_v1.5.0.zip`
2. 解压到任意目录（路径不要放在需要管理员权限的位置）
3. 双击 `app/手机投屏助手.exe`
4. 手机开启「USB 调试」并用数据线连接，或开启「无线调试」完成一次配对；点击「刷新」→「连接并投屏」

完整说明见 [docs/USAGE.md](docs/USAGE.md)。
后续升级方案与 GitHub 同类项目对标见 [docs/ROADMAP.md](docs/ROADMAP.md)。

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
