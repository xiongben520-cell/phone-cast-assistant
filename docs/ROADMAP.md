# 后续升级方案（基于 GitHub 同类项目对标）

调研时间：2026-11-22。方法：在 GitHub 按 scrcpy GUI、Android MCP、移动 Agent 三个方向检索，
阅读各项目 README，提取功能矩阵，再结合本项目现状排优先级。

## 1. 对标项目

### 1.1 scrcpy GUI 封装类

| 项目 | Stars | 特点（值得借鉴） |
|---|---:|---|
| [SimonAKing/scrcpy-gui](https://github.com/SimonAKing/scrcpy-gui) | 4.2k | 官方 adb/scrcpy 随包、命名配置档、每设备别名与独立配置、保存的无线地址、自动启动、多设备同时投屏、录制、触摸指示、ADB 动作回放、额外启动参数、真实 stderr 日志、最小化到托盘、多语言、Electron 沙箱 + 参数安全启动 |
| [kil0bit-kb/scrcpy-gui](https://github.com/kil0bit-kb/scrcpy-gui) | 1.5k | Tauri + React 现代 UI、自动检查并更新 scrcpy 二进制、OTG 精确输入、渲染器选择、摄像头模式、虚拟显示桌面模式、文件管理 |
| [Shrey113/Adb-Device-Manager-2](https://github.com/Shrey113/Adb-Device-Manager-2) | 667 | ADB Mode + App Mode 双形态；DeX / 多应用虚拟显示、APK 安装、通讯录/短信/通话记录、相机画面、通知同步、媒体控制、相册、双向文件传输 |

### 1.2 AI Agent 控制手机类

| 项目 | Stars | 特点（值得借鉴） |
|---|---:|---|
| [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 39.6k | 多模态 GUI Agent 栈，视觉定位 + 操作闭环 |
| [minitap-ai/mobile-use](https://github.com/minitap-ai/mobile-use) | 3.4k | 自然语言控制、UI 感知、从 App 抽取结构化数据（JSON）、多模型可插拔 |
| [CursorTouch/Android-MCP](https://github.com/CursorTouch/Android-MCP) | 939 | **最值得直接参考的工具设计**：`Snapshot` 返回带编号控件与点击坐标 + 标注截图；`ClickBySelector` 按 text/resourceId/className/description 定位；`WaitForElement`；`Type` 配 ADBKeyboard 解决非 ASCII 输入；`Device(action=switch, to=wifi/usb)` 有线无线互切；截图量化降体积 |
| [ghost-in-the-droid/android-agent](https://github.com/ghost-in-the-droid/android-agent) | 431 | 多端（Android/iOS）× 多模型 × 多驱动（MCP/LangChain/LlamaIndex/CLI/REST/Web）；实时画面流 + 画面上点按；多设备 farm 与逐设备任务队列；YAML 元素定义与动作/工作流；BFS 自动探索；LLM 辅助生成技能；Skill Hub；human-in-the-loop（验证码由人处理，Agent 继续） |
| [minhalvp/android-mcp-server](https://github.com/minhalvp/android-mcp-server) | 814 | ADB 命令执行、截图、UI 布局分析、包管理，单/多设备选择 |

## 2. 本项目现状对照

**已有优势**（同类 GUI 项目少有的）：
- 解压即用，adb/scrcpy 全部内置，无需 PATH
- USB + Wi-Fi（配对码 / 扫码）双通道，配对后自动 connect 的坑已填
- 每次连接按实测带宽 ×80% 定码率，固定 60fps
- 音频整路转发、测试音、手机亮/黑屏、置顶、贴图跟随、旋转锚点
- 连接过程不改动手机音量/静音

**明显短板**（对标后）：
1. "Agent 控制手机"目前只是 README 里的 adb 命令示例，**不是产品能力**
2. 单设备、无配置档/别名/自动连接
3. 无录制、截图、触摸指示、ADB 动作回放
4. 无结构化界面感知（控件树 + 坐标 + 标注截图），Agent 只能靠猜坐标
5. 中文输入无解（`input text` 不支持中文）
6. 无人在回路的可视化管控（Agent 操作时人只能干看）

## 3. 升级方案（按优先级）

### P0 — 把「Agent 控制」做成产品能力（建议 1–2 周）

目标：让 OpenCode / Claude Code / Cursor 等客户端通过 MCP 直接驱动手机，
同时保留本项目独有的「人看得见、随时接管」。

1. **内置 Agent Bridge（MCP Server）**
   - 新增 `agent_bridge.py`，由 GUI 的「Agent 桥接」开关按需拉起，stdio + HTTP 双模式
   - 与 GUI 共享 `device_state.json`：当前串号、连接类型、码率、连接时间
   - 复用 GUI 已建立的 adb 连接，不重复配对/连接

2. **工具集设计**（参考 CursorTouch/Android-MCP）
   - `list_devices` / `connect` / `switch_transport`（usb ↔ wifi）
   - `snapshot` → 返回 `{控件文字, 编号, bbox 中心坐标, 标注截图路径}`；截图可量化降体积
   - `tap` / `long_press` / `swipe` / `drag` / `press(back,home,recent,...)`
   - `click_by_selector(text|resourceId|className|description)` —— 比纯坐标稳
   - `type` / `clear`，`wait_for_element`
   - `screenshot`、`shell`（**白名单**：仅限 dump/scroll/亮度等只读或低危命令）

3. **中文与非 ASCII 输入**
   - 随包附带 ADBKeyboard APK（注意其许可证），Bridge 检测并自动启用
   - 剪贴板方案作为回退

4. **安全与人在回路**（差异化核心）
   - shell 白名单 + 操作审计日志，所有 Agent 动作实时显示在 GUI 面板
   - 敏感步骤（验证码、支付、密码、权限弹窗）自动暂停，等人在投屏窗口点「继续」
   - GUI 提供「一键暂停 Agent / 立即接管」

> P0 完成后，本项目的定位从「投屏器」升级为**带人工监管的手机自动化控制台**，
> 这是纯 MCP 项目和纯 GUI 项目都没有的交叉点。

### P1 — GUI 能力补齐（建议 2–4 周）

- 设备配置档：别名、保存的无线地址、按设备记忆参数、启动自动连接
- 多设备同时投屏：多 scrcpy 实例 + 窗口标题含别名 + 重复进程保护（参考 SimonAKing）
- 录制（`--record`）、截图按钮、触摸指示、`--max-size` 等常用参数的可视化配置
- 「额外启动参数」输入框 + 参数安全校验（禁止注入、统一 list 传参）
- 真实启动状态灯 + stderr 日志面板（已有日志文件，补 UI）
- 最小化到系统托盘、后台常驻与快速唤出

### P2 — 稳定性与分发（建议 1–2 月）

- scrcpy / adb 版本检查与一键更新（参考 kil0bit-kb）
- 断线自动重连 watchdog 增强（目前 5 秒巡检，可加指数退避与 Wi-Fi 切换恢复）
- GitHub Release 自动更新提示
- 参数启动全面改为 list 传参，消除引号/空格串号注入风险
- 补充 CI：语法检查、脱敏扫描、打包回归

### P3 — 探索性方向（按需）

- 虚拟显示 / 桌面模式（`scrcpy --new-display`）、相机模式
- 技能系统：YAML 元素定义 + 动作工作流 + 技能市场（参考 ghost-in-the-droid）
- 多设备 farm 与任务队列（适合批量无人值守）
- 平台扩展到 macOS / Linux（工作量较大，需重新处理音频路由与亮屏快捷键）
- 通知同步 / 文件传输 / 媒体控制（需要 companion App，慎重评估）

## 4. 不建议做的事

- 不自研大模型或 Agent 推理框架：模型交给 MCP 客户端，本项目专注「设备通道 + 人在回路」
- 不盲目堆 App Mode（短信/通话/相册）：偏离投屏与自动化主线，且需要 companion App 与权限
- 不做无监管的完全自动：支付、验证码、隐私界面必须有人工确认点

## 5. 衡量指标

| 指标 | 目标 |
|---|---|
| Agent 任务成功率（可用 AndroidWorld 等基准） | 随 snapshot 质量提升持续优化 |
| 连接成功率 / 断线恢复时间 | 连接成功率 > 95%，恢复 < 10s |
| `snapshot` 端到端耗时 | < 500ms |
| 人工接管次数 | 敏感步骤 100% 触发暂停，无误操作 |
| 码率利用率 | 持续保持实测带宽的 80% |
